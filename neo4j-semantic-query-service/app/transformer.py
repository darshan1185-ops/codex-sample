from __future__ import annotations
import datetime as dt
import json
from typing import Any
from .config import Settings
from .normalization import as_list, canonical_hash, clean_properties, first_nonempty, normalize, risk_rank, unique_strings


class ProfileTransformer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract_profiles(self, document: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(document, list):
            profiles = document
        elif isinstance(document, dict) and isinstance(document.get("profiles"), list):
            profiles = document["profiles"]
        elif isinstance(document, dict) and (document.get("api_id") or document.get("apiId")):
            profiles = [document]
        else:
            raise ValueError("Document must contain a profiles array or a single API profile.")

        if len(profiles) > self.settings.max_ingestion_profiles:
            raise ValueError("Profile count exceeds MAX_INGESTION_PROFILES.")
        if not all(isinstance(profile, dict) for profile in profiles):
            raise ValueError("Every profile must be a JSON object.")
        return profiles

    def transform_document(self, document: dict[str, Any] | list[dict[str, Any]], *, source: str, run_id: str) -> list[dict[str, Any]]:
        return [self.transform_profile(profile, source=source, run_id=run_id) for profile in self.extract_profiles(document)]

    def transform_profile(self, profile: dict[str, Any], *, source: str, run_id: str) -> dict[str, Any]:
        semantic = profile.get("semanticProfile") or {}
        inferred = profile.get("inferred") or {}
        api_meta = profile.get("api") or {}

        api_id = str(first_nonempty(profile.get("api_id"), profile.get("apiId"), api_meta.get("id"), default="")).strip()
        if not api_id:
            raise ValueError("API profile is missing api_id.")

        method = str(first_nonempty(profile.get("method"), api_meta.get("method"), default="GET")).upper()
        path = str(first_nonempty(profile.get("path"), api_meta.get("path"), default="/"))
        normalized_path = str(first_nonempty(profile.get("normalized_path"), profile.get("normalizedPath"), path))
        domain = str(first_nonempty(semantic.get("domain"), inferred.get("primary_domain"), default="Unclassified"))
        capability = str(first_nonempty(semantic.get("businessCapability"), inferred.get("primary_capability"), default="Unclassified"))
        primary_entity = str(first_nonempty(semantic.get("entity"), default="Unclassified"))
        entities = unique_strings([primary_entity, *as_list(semantic.get("entities")), *as_list(inferred.get("entities"))])
        action = str(first_nonempty(semantic.get("action"), default="Unclassified"))
        workflow = str(first_nonempty(semantic.get("workflow"), default="Unclassified"))
        workflow_stage = str(first_nonempty(semantic.get("workflowStage"), default="Unclassified"))
        operation_type = str(first_nonempty(semantic.get("operationType"), default="Unclassified"))
        risk_level = str(first_nonempty(semantic.get("riskLevel"), inferred.get("risk"), default="Low"))
        data_sensitivity = str(first_nonempty(semantic.get("dataSensitivity"), default="Low"))
        criticality = str(first_nonempty(semantic.get("criticality"), risk_level))
        exposure = str(first_nonempty(semantic.get("exposure"), api_meta.get("exposure"), default="Unknown"))
        data_categories = unique_strings(as_list(semantic.get("dataCategories")))
        compliance = unique_strings([*as_list(semantic.get("compliance")), *as_list(inferred.get("compliance"))])
        consumers = self._consumers(profile, semantic)
        dependencies = self._dependencies(profile)
        violations = self._violations(profile, api_id)
        workflows = [{"name": workflow, "stage": workflow_stage, "role": "Participant", "stageId": canonical_hash({"workflow": workflow, "stage": workflow_stage})}]
        gateways = unique_strings([*as_list(profile.get("gateway")), *as_list(profile.get("gateways")), *as_list(api_meta.get("gateway"))])
        teams = unique_strings([*as_list(profile.get("team")), *as_list(profile.get("teams")), *as_list(profile.get("ownerTeam"))])
        environments = unique_strings([*as_list(profile.get("environment")), *as_list(profile.get("environments"))])
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        fingerprint = canonical_hash({
            "domain": normalize(domain), "capability": normalize(capability),
            "entities": sorted(normalize(value) for value in entities),
            "action": normalize(action), "workflow": normalize(workflow),
            "workflowStage": normalize(workflow_stage), "operationType": normalize(operation_type),
        })

        semantic_text = " ".join(unique_strings([api_id, method, path, domain, capability, *entities, action, workflow, workflow_stage, operation_type, risk_level, data_sensitivity, criticality, exposure, *data_categories, *compliance]))

        api = clean_properties({
            "id": api_id, "method": method, "path": path, "normalizedPath": normalized_path,
            "domain": domain, "businessCapability": capability, "primaryEntity": primary_entity,
            "action": action, "workflow": workflow, "workflowStage": workflow_stage,
            "operationType": operation_type, "riskLevel": risk_level, "riskRank": risk_rank(risk_level),
            "dataSensitivity": data_sensitivity, "criticality": criticality, "exposure": exposure,
            "containsPII": bool(inferred.get("containsPII")), "containsPCI": bool(inferred.get("containsPCI")),
            "containsFinancialData": bool(inferred.get("containsFinancialData")),
            "securitySensitive": bool(inferred.get("securitySensitive")),
            "privilegedOperation": bool(inferred.get("privilegedOperation")),
            "riskScore": int(inferred.get("risk_score", 0) or 0), "confidence": float(inferred.get("confidence", 0) or 0),
            "semanticFingerprint": fingerprint, "semanticText": semantic_text, "profileHash": canonical_hash(profile),
            "source": source, "ingestionRunId": run_id, "updatedAt": now,
        })
        if self.settings.store_raw_profile:
            api["rawProfileJson"] = json.dumps(profile, ensure_ascii=False, separators=(",", ":"), default=str)

        return {"api": api, "domains": [domain], "capabilities": [capability], "entities": entities, "workflows": workflows, "dataCategories": data_categories, "compliance": compliance, "consumers": consumers, "violations": violations, "dependencies": dependencies, "gateways": gateways, "teams": teams, "environments": environments}

    def _consumers(self, profile: dict[str, Any], semantic: dict[str, Any]) -> list[dict[str, Any]]:
        names = unique_strings([*as_list(semantic.get("consumers")), *as_list(profile.get("consumers"))])
        interactions = first_nonempty(semantic.get("consumerInteractions"), profile.get("consumerInteractions"), default=[])
        mapping: dict[str, dict[str, Any]] = {}
        for item in interactions if isinstance(interactions, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(first_nonempty(item.get("consumer"), item.get("name"), default="")).strip()
            if name:
                names = unique_strings([*names, name])
                mapping[normalize(name)] = item
        return [{"name": name, "properties": clean_properties({key: mapping.get(normalize(name), {}).get(key) for key in ("interactionType", "authentication", "environment", "channel", "accessPattern", "requestVolume30d", "errorRate", "averageLatencyMs", "approved")})} for name in names]

    def _violations(self, profile: dict[str, Any], api_id: str) -> list[dict[str, Any]]:
        output = []
        for item in as_list(profile.get("violated")):
            if not isinstance(item, dict):
                continue
            control_id = str(first_nonempty(item.get("control_id"), item.get("controlId"), item.get("rule_id"), default="UNKNOWN"))
            violation_id = f"{api_id}::{control_id}"
            output.append({"id": violation_id, "properties": clean_properties({"id": violation_id, "apiId": api_id, "controlId": control_id, "ruleId": first_nonempty(item.get("rule_id"), item.get("ruleId")), "severity": item.get("severity", "LOW"), "message": item.get("message", ""), "standards": unique_strings(as_list(item.get("standards")))})})
        return output

    def _dependencies(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        semantic = profile.get("semanticProfile") or {}
        inferred = profile.get("inferred") or {}
        raw = first_nonempty(profile.get("dependencies"), semantic.get("dependencies"), inferred.get("dependencies"), default=[])
        output: dict[str, dict[str, Any]] = {}
        for item in as_list(raw):
            if isinstance(item, str):
                dependency_id, properties = item, {}
            elif isinstance(item, dict):
                dependency_id = str(first_nonempty(item.get("apiId"), item.get("api_id"), item.get("id"), item.get("target"), default=""))
                properties = clean_properties({key: item.get(key) for key in ("dependencyType", "protocol", "required", "runtimeObserved", "confidence")})
            else:
                continue
            if dependency_id:
                output[normalize(dependency_id)] = {"apiId": dependency_id, "properties": properties}
        return list(output.values())
