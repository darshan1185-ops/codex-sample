from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any, Literal

from .config import Settings
from .models import SearchFilters
from .neo4j_client import Neo4jClient
from .normalization import lower_text, normalize, risk_rank, unique_strings
from .transformer import ProfileTransformer

MANAGED_RELATIONSHIPS = ["IN_DOMAIN", "REPRESENTS", "MANIPULATES", "PARTICIPATES_IN", "OPERATES_AT", "HANDLES", "SUBJECT_TO", "HAS_VIOLATION", "DEPENDS_ON", "EXPOSED_THROUGH", "OWNED_BY", "DEPLOYED_IN"]

CLEAN_RELATIONSHIPS = """
UNWIND $apiIds AS apiId
MATCH (api:API {id: apiId})
OPTIONAL MATCH (api)-[outgoing]->()
WITH api, [r IN collect(outgoing) WHERE type(r) IN $relationshipTypes] AS rels
FOREACH (r IN rels | DELETE r)
WITH api
OPTIONAL MATCH (:Consumer)-[incoming:CONSUMES]->(api)
WITH collect(incoming) AS incomingRels
FOREACH (r IN incomingRels | DELETE r)
"""

UPSERT_GRAPH = """
UNWIND $rows AS row
MERGE (api:API {id: row.api.id})
SET api = row.api
SET api.stub = false
WITH api, row
FOREACH (name IN row.domains | MERGE (n:BusinessDomain {name: name}) MERGE (api)-[:IN_DOMAIN]->(n))
FOREACH (name IN row.capabilities | MERGE (n:BusinessCapability {name: name}) MERGE (api)-[:REPRESENTS]->(n))
FOREACH (name IN row.entities | MERGE (n:Entity {name: name}) MERGE (api)-[:MANIPULATES]->(n))
FOREACH (wf IN row.workflows |
  MERGE (w:Workflow {name: wf.name})
  MERGE (s:WorkflowStage {id: wf.stageId})
  SET s.name = wf.stage, s.workflowName = wf.name
  MERGE (s)-[:STAGE_OF]->(w)
  MERGE (api)-[p:PARTICIPATES_IN]->(w)
  SET p.stage = wf.stage, p.role = wf.role
  MERGE (api)-[:OPERATES_AT]->(s)
)
FOREACH (name IN row.dataCategories | MERGE (n:DataCategory {name: name}) MERGE (api)-[:HANDLES]->(n))
FOREACH (name IN row.compliance | MERGE (n:ComplianceStandard {name: name}) MERGE (api)-[:SUBJECT_TO]->(n))
FOREACH (item IN row.consumers | MERGE (n:Consumer {name: item.name}) MERGE (n)-[r:CONSUMES]->(api) SET r += item.properties)
FOREACH (item IN row.violations | MERGE (n:Violation {id: item.id}) SET n = item.properties MERGE (api)-[:HAS_VIOLATION]->(n))
FOREACH (item IN row.dependencies | MERGE (n:API {id: item.apiId}) ON CREATE SET n.stub = true MERGE (api)-[r:DEPENDS_ON]->(n) SET r += item.properties)
FOREACH (name IN row.gateways | MERGE (n:Gateway {name: name}) MERGE (api)-[:EXPOSED_THROUGH]->(n))
FOREACH (name IN row.teams | MERGE (n:Team {name: name}) MERGE (api)-[:OWNED_BY]->(n))
FOREACH (name IN row.environments | MERGE (n:Environment {name: name}) MERGE (api)-[:DEPLOYED_IN]->(n))
"""

PROJECTION = """
RETURN api{.*} AS api,
[(api)-[:MANIPULATES]->(n:Entity) | n.name] AS entities,
[(api)-[r:PARTICIPATES_IN]->(n:Workflow) | {name:n.name, stage:r.stage, role:r.role}] AS workflows,
[(api)-[:HANDLES]->(n:DataCategory) | n.name] AS dataCategories,
[(api)-[:SUBJECT_TO]->(n:ComplianceStandard) | n.name] AS compliance,
[(n:Consumer)-[r:CONSUMES]->(api) | {name:n.name, interactionType:r.interactionType, authentication:r.authentication, environment:r.environment, channel:r.channel, accessPattern:r.accessPattern, requestVolume30d:r.requestVolume30d, errorRate:r.errorRate, averageLatencyMs:r.averageLatencyMs, approved:r.approved}] AS consumers,
[(api)-[:HAS_VIOLATION]->(n:Violation) | n{.*}] AS violations,
[(api)-[r:DEPENDS_ON]->(n:API) | {apiId:n.id, method:n.method, path:n.path, domain:n.domain, businessCapability:n.businessCapability, riskLevel:n.riskLevel, dependencyType:r.dependencyType, protocol:r.protocol, required:r.required}] AS dependencies,
[(api)-[:EXPOSED_THROUGH]->(n:Gateway) | n.name] AS gateways,
[(api)-[:OWNED_BY]->(n:Team) | n.name] AS teams,
[(api)-[:DEPLOYED_IN]->(n:Environment) | n.name] AS environments
"""


class SearchQueryBuilder:
    @staticmethod
    def build(filters: SearchFilters) -> tuple[str, dict[str, Any]]:
        clauses = ["coalesce(api.stub, false) = false"]
        params: dict[str, Any] = {"limit": filters.limit, "offset": filters.offset}
        fields = (("apiIds", "id", filters.api_ids), ("methods", "method", filters.methods), ("domains", "domain", filters.domains), ("capabilities", "businessCapability", filters.capabilities), ("workflows", "workflow", filters.workflows), ("workflowStages", "workflowStage", filters.workflow_stages), ("actions", "action", filters.actions), ("operationTypes", "operationType", filters.operation_types), ("riskLevels", "riskLevel", filters.risk_levels), ("sensitivities", "dataSensitivity", filters.sensitivities), ("criticalities", "criticality", filters.criticalities), ("exposures", "exposure", filters.exposures))
        for parameter, prop, values in fields:
            if values:
                params[parameter] = [lower_text(v) for v in values]
                clauses.append(f"toLower(api.{prop}) IN ${parameter}")
        if filters.path_contains:
            params["pathContains"] = lower_text(filters.path_contains)
            clauses.append("toLower(api.path) CONTAINS $pathContains")
        if filters.min_risk:
            params["minimumRiskRank"] = risk_rank(filters.min_risk)
            clauses.append("coalesce(api.riskRank, 0) >= $minimumRiskRank")
        for prop, expected in (("containsPII", filters.contains_pii), ("containsPCI", filters.contains_pci), ("containsFinancialData", filters.contains_financial_data), ("securitySensitive", filters.security_sensitive), ("privilegedOperation", filters.privileged_operation)):
            if expected is not None:
                key = f"flag_{prop}"
                params[key] = expected
                clauses.append(f"coalesce(api.{prop}, false) = ${key}")
        if filters.has_violations is True:
            clauses.append("EXISTS { MATCH (api)-[:HAS_VIOLATION]->(:Violation) }")
        elif filters.has_violations is False:
            clauses.append("NOT EXISTS { MATCH (api)-[:HAS_VIOLATION]->(:Violation) }")
        if filters.free_text:
            params["freeText"] = normalize(filters.free_text)
            clauses.append("toLower(api.semanticText) CONTAINS $freeText")
        return " AND ".join(f"({c})" for c in clauses), params


class GraphRepository:
    def __init__(self, client: Neo4jClient, settings: Settings, transformer: ProfileTransformer) -> None:
        self.client = client
        self.settings = settings
        self.transformer = transformer

    async def ingest(self, document: dict[str, Any] | list[dict[str, Any]], *, source: str, dry_run: bool = False) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        rows = self.transformer.transform_document(document, source=source, run_id=run_id)
        if dry_run:
            return {"runId": run_id, "status": "VALIDATED", "dryRun": True, "profileCount": len(rows), "apiIds": [row["api"]["id"] for row in rows]}
        for offset in range(0, len(rows), self.settings.ingestion_batch_size):
            await self.client.execute_write(self._write_batch, rows[offset:offset + self.settings.ingestion_batch_size])
        return {"runId": run_id, "status": "COMPLETED", "dryRun": False, "profileCount": len(rows), "startedAt": started, "completedAt": dt.datetime.now(dt.timezone.utc).isoformat()}

    @staticmethod
    async def _write_batch(tx: Any, rows: list[dict[str, Any]]) -> None:
        ids = [row["api"]["id"] for row in rows]
        result = await tx.run(CLEAN_RELATIONSHIPS, apiIds=ids, relationshipTypes=MANAGED_RELATIONSHIPS)
        await result.consume()
        result = await tx.run(UPSERT_GRAPH, rows=rows)
        await result.consume()

    async def get_api(self, api_id: str) -> dict[str, Any] | None:
        rows = await self.client.read(f"MATCH (api:API {{id:$apiId}}) WHERE coalesce(api.stub,false)=false {PROJECTION}", {"apiId": api_id})
        return self._hydrate(rows[0]) if rows else None

    async def search(self, filters: SearchFilters) -> dict[str, Any]:
        where, params = SearchQueryBuilder.build(filters)
        count_query = f"MATCH (api:API) WHERE {where} RETURN count(api) AS total"
        result_query = f"MATCH (api:API) WHERE {where} WITH api ORDER BY coalesce(api.riskRank,0) DESC, api.id SKIP $offset LIMIT $limit {PROJECTION}"
        counts, rows = await asyncio.gather(self.client.read(count_query, params), self.client.read(result_query, params))
        return {"total": int(counts[0]["total"]) if counts else 0, "limit": filters.limit, "offset": filters.offset, "items": [self._hydrate(row) for row in rows]}

    async def catalog(self) -> dict[str, list[str]]:
        rows = await self.client.read("""
        MATCH (api:API) WHERE coalesce(api.stub,false)=false
        WITH collect(DISTINCT api.id) AS apiIds, collect(DISTINCT api.method) AS methods, collect(DISTINCT api.domain) AS domains, collect(DISTINCT api.businessCapability) AS capabilities, collect(DISTINCT api.workflow) AS workflows, collect(DISTINCT api.workflowStage) AS workflowStages, collect(DISTINCT api.action) AS actions, collect(DISTINCT api.operationType) AS operationTypes, collect(DISTINCT api.riskLevel) AS riskLevels, collect(DISTINCT api.dataSensitivity) AS sensitivities, collect(DISTINCT api.criticality) AS criticalities, collect(DISTINCT api.exposure) AS exposures
        CALL { MATCH (n:Entity) RETURN collect(DISTINCT n.name) AS entities }
        CALL { MATCH (n:DataCategory) RETURN collect(DISTINCT n.name) AS dataCategories }
        CALL { MATCH (n:ComplianceStandard) RETURN collect(DISTINCT n.name) AS compliance }
        CALL { MATCH (n:Consumer) RETURN collect(DISTINCT n.name) AS consumers }
        RETURN apiIds,methods,domains,capabilities,workflows,workflowStages,actions,operationTypes,riskLevels,sensitivities,criticalities,exposures,entities,dataCategories,compliance,consumers
        """)
        return {key: sorted(unique_strings(value or []), key=normalize) for key, value in rows[0].items()} if rows else {}

    async def identities(self) -> list[dict[str, Any]]:
        return await self.client.read("MATCH (api:API) WHERE coalesce(api.stub,false)=false RETURN api.id AS apiId, api.method AS method, api.path AS path, api.normalizedPath AS normalizedPath ORDER BY api.id")

    async def duplicate_groups(self, limit: int) -> list[dict[str, Any]]:
        return await self.client.read("MATCH (api:API) WHERE coalesce(api.stub,false)=false AND api.semanticFingerprint IS NOT NULL WITH api.semanticFingerprint AS fingerprint, collect({apiId:api.id,method:api.method,path:api.path,businessCapability:api.businessCapability}) AS apis WHERE size(apis)>1 RETURN fingerprint,size(apis) AS apiCount,apis ORDER BY apiCount DESC LIMIT $limit", {"limit": limit})

    async def capability_groups(self, limit: int) -> list[dict[str, Any]]:
        return await self.client.read("MATCH (api:API) WHERE coalesce(api.stub,false)=false WITH api.businessCapability AS businessCapability, collect({apiId:api.id,method:api.method,path:api.path,riskLevel:api.riskLevel}) AS apis WHERE businessCapability IS NOT NULL AND size(apis)>1 RETURN businessCapability,size(apis) AS apiCount,apis ORDER BY apiCount DESC LIMIT $limit", {"limit": limit})

    async def impact(self, api_id: str, *, direction: Literal["dependencies", "dependents"], max_depth: int) -> list[dict[str, Any]]:
        depth = min(max(1, max_depth), self.settings.maximum_dependency_depth)
        pattern = f"(start)-[:DEPENDS_ON*1..{depth}]->(related)" if direction == "dependencies" else f"(start)<-[:DEPENDS_ON*1..{depth}]-(related)"
        return await self.client.read(f"MATCH path=(start:API {{id:$apiId}}){pattern} WITH related,min(length(path)) AS distance,head(collect([n IN nodes(path)|n.id])) AS pathIds RETURN related.id AS apiId,related.method AS method,related.path AS path,related.businessCapability AS businessCapability,related.riskLevel AS riskLevel,distance,pathIds ORDER BY distance,apiId", {"apiId": api_id})

    async def dependency_path(self, from_api_id: str, to_api_id: str, *, max_depth: int) -> dict[str, Any] | None:
        depth = min(max(1, max_depth), self.settings.maximum_dependency_depth)
        rows = await self.client.read(f"MATCH path=shortestPath((a:API {{id:$fromApiId}})-[:DEPENDS_ON*..{depth}]-(b:API {{id:$toApiId}})) RETURN [n IN nodes(path)|{{apiId:n.id,method:n.method,path:n.path,businessCapability:n.businessCapability,riskLevel:n.riskLevel}}] AS nodes,[r IN relationships(path)|type(r)] AS relationships,length(path) AS distance", {"fromApiId": from_api_id, "toApiId": to_api_id})
        return rows[0] if rows else None

    async def prune_orphans(self) -> int:
        rows = await self.client.write("MATCH (n) WHERE (n:BusinessDomain OR n:BusinessCapability OR n:Entity OR n:Workflow OR n:WorkflowStage OR n:Consumer OR n:DataCategory OR n:ComplianceStandard OR n:Violation OR n:Gateway OR n:Team OR n:Environment) AND NOT (n)--() WITH collect(n) AS nodes FOREACH (x IN nodes|DETACH DELETE x) RETURN size(nodes) AS deleted")
        return int(rows[0]["deleted"]) if rows else 0

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        api = dict(row.get("api") or {})
        consumers = row.get("consumers") or []
        return {"apiId": api.get("id"), "method": api.get("method"), "path": api.get("path"), "normalizedPath": api.get("normalizedPath"), "semanticProfile": {"domain": api.get("domain"), "businessCapability": api.get("businessCapability"), "entity": api.get("primaryEntity"), "entities": unique_strings(row.get("entities") or []), "action": api.get("action"), "workflow": api.get("workflow"), "workflowStage": api.get("workflowStage"), "workflows": row.get("workflows") or [], "operationType": api.get("operationType"), "riskLevel": api.get("riskLevel"), "dataSensitivity": api.get("dataSensitivity"), "criticality": api.get("criticality"), "exposure": api.get("exposure"), "dataCategories": unique_strings(row.get("dataCategories") or []), "compliance": unique_strings(row.get("compliance") or []), "consumers": unique_strings(item.get("name") for item in consumers if isinstance(item, dict)), "consumerInteractions": consumers}, "flags": {"containsPII": api.get("containsPII", False), "containsPCI": api.get("containsPCI", False), "containsFinancialData": api.get("containsFinancialData", False), "securitySensitive": api.get("securitySensitive", False), "privilegedOperation": api.get("privilegedOperation", False)}, "violations": row.get("violations") or [], "dependencies": row.get("dependencies") or [], "gateways": unique_strings(row.get("gateways") or []), "teams": unique_strings(row.get("teams") or []), "environments": unique_strings(row.get("environments") or [])}
