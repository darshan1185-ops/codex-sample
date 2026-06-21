from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from .config import Settings
from .models import NaturalLanguageQueryRequest, QueryPlan, SearchFilters
from .planner import SemanticPlanner
from .repository import GraphRepository


class GroundedAnswerBuilder:
    def single_api_answer(self, intent: str, profile: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        semantic = profile["semanticProfile"]
        label = f'{profile["method"]} {profile["path"]} ({profile["apiId"]})'
        if intent == "capability":
            return f'{label} represents the "{semantic["businessCapability"]}" business capability in the "{semantic["domain"]}" domain.', [{"apiId": profile["apiId"], "relationship": "REPRESENTS", "value": semantic["businessCapability"]}]
        if intent == "entities":
            values = semantic.get("entities", [])
            return f'{label} manipulates or references {", ".join(values) if values else "no classified entities"}.', [{"apiId": profile["apiId"], "relationship": "MANIPULATES", "value": values}]
        if intent == "workflow":
            return f'{label} participates in "{semantic["workflow"]}" at the "{semantic["workflowStage"]}" stage.', [{"apiId": profile["apiId"], "relationship": "PARTICIPATES_IN", "value": semantic["workflow"]}]
        if intent == "risk":
            violations = profile.get("violations", [])
            answer = f'{label} is "{semantic["riskLevel"]}" risk with "{semantic["dataSensitivity"]}" data sensitivity and "{semantic["criticality"]}" criticality. It has {len(violations)} recorded control violation(s).'
            return answer, [{"apiId": profile["apiId"], "relationship": "HAS_VIOLATION", "value": violations}]
        if intent == "consumers":
            interactions = semantic.get("consumerInteractions", [])
            names = [item.get("name", "Unknown consumer") for item in interactions]
            answer = f'{label} is consumed by {"; ".join(names)}.' if names else f"{label} has no consumers recorded in the graph."
            return answer, [{"apiId": profile["apiId"], "relationship": "CONSUMED_BY", "value": interactions}]
        entities = ", ".join(semantic.get("entities", [])) or "unclassified entities"
        answer = f'{label} is a {semantic["exposure"]} {str(semantic["operationType"]).lower()} API in the {semantic["domain"]} domain. It represents {semantic["businessCapability"]}, performs {semantic["action"]}, acts on {entities}, participates in {semantic["workflow"]} at the {semantic["workflowStage"]} stage, and is {semantic["riskLevel"]} risk.'
        return answer, [{"apiId": profile["apiId"], "relationship": "SEMANTIC_PROFILE", "value": semantic}]


class SemanticService:
    def __init__(self, repository: GraphRepository, planner: SemanticPlanner, answer_builder: GroundedAnswerBuilder, settings: Settings) -> None:
        self.repository = repository
        self.planner = planner
        self.answer_builder = answer_builder
        self.settings = settings
        self._catalog: dict[str, list[str]] | None = None
        self._identities: list[dict[str, Any]] | None = None
        self._cache_expires_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def invalidate_cache(self) -> None:
        async with self._cache_lock:
            self._catalog = None
            self._identities = None
            self._cache_expires_at = 0.0

    async def query(self, request: NaturalLanguageQueryRequest) -> dict[str, Any]:
        catalog, identities = await self._catalog_data()
        plan = self.planner.plan(request.question, catalog=catalog, identities=identities, explicit_api_id=request.api_id, limit=min(request.limit, self.settings.maximum_query_limit))
        if plan.intent == "duplicates":
            items = await self.repository.duplicate_groups(request.limit)
            return self._response(request.question, f"Found {len(items)} semantic duplicate group(s).", plan, items)
        if plan.intent == "capability_groups":
            items = await self.repository.capability_groups(request.limit)
            return self._response(request.question, f"Found {len(items)} capability group(s).", plan, items)
        if plan.intent in {"impact_dependencies", "impact_dependents"}:
            if not plan.target_api_id:
                return self._response(request.question, "A specific API ID is required.", plan, [])
            direction: Literal["dependencies", "dependents"] = "dependents" if plan.intent == "impact_dependents" else "dependencies"
            items = await self.repository.impact(plan.target_api_id, direction=direction, max_depth=self.settings.maximum_dependency_depth)
            return self._response(request.question, f"Found {len(items)} {direction}.", plan, items)
        if plan.intent == "dependency_path":
            if not plan.target_api_id or not plan.secondary_api_id:
                return self._response(request.question, "Two API IDs are required.", plan, [])
            path = await self.repository.dependency_path(plan.target_api_id, plan.secondary_api_id, max_depth=self.settings.maximum_dependency_depth)
            return self._response(request.question, "Dependency path found." if path else "No dependency path found.", plan, [path] if path else [])
        filters = SearchFilters(**plan.filters)
        filters.limit = min(request.limit, self.settings.maximum_query_limit)
        result = await self.repository.search(filters)
        items = result["items"]
        if not items:
            return self._response(request.question, "No profiled APIs matched the question.", plan, [])
        if plan.target_api_id and len(items) == 1:
            answer, evidence = self.answer_builder.single_api_answer(plan.intent, items[0])
            return self._response(request.question, answer, plan, items, evidence)
        return self._response(request.question, f"Found {result['total']} profiled API(s).", plan, items)

    async def _catalog_data(self) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
        if self._catalog is not None and self._identities is not None and time.monotonic() < self._cache_expires_at:
            return self._catalog, self._identities
        async with self._cache_lock:
            self._catalog, self._identities = await asyncio.gather(self.repository.catalog(), self.repository.identities())
            self._cache_expires_at = time.monotonic() + self.settings.catalog_cache_seconds
            return self._catalog, self._identities

    @staticmethod
    def _response(question: str, answer: str, plan: QueryPlan, items: list[Any], evidence: list[Any] | None = None) -> dict[str, Any]:
        return {"answer": answer, "question": question, "interpretation": plan.model_dump(), "results": {"total": len(items), "items": items}, "evidence": evidence or items}
