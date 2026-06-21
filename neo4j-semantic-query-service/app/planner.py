from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .models import QueryPlan, SearchFilters
from .normalization import normalize, unique_strings

INTENTS = {
    "duplicates": ("semantic duplicate", "duplicate apis"),
    "capability_groups": ("group by capability", "same business capability"),
    "dependency_path": ("dependency path", "path between"),
    "impact_dependents": ("blast radius", "what is impacted", "dependents"),
    "impact_dependencies": ("what does this depend on", "dependencies of"),
    "capability": ("business capability", "what does it represent"),
    "entities": ("what entities", "manipulates", "acts on"),
    "workflow": ("what workflow", "workflow stage", "lifecycle"),
    "risk": ("security sensitivity", "data sensitivity", "what risk", "criticality"),
    "consumers": ("how consumers interact", "who consumes", "who calls"),
    "violations": ("violations", "control failures", "non compliant"),
    "semantic_summary": ("semantic meaning", "what does this api do", "describe this api"),
}


class SemanticPlanner:
    def plan(self, question: str, *, catalog: dict[str, list[str]], identities: list[dict[str, Any]], explicit_api_id: str | None, limit: int) -> QueryPlan:
        normalized = normalize(question)
        intent = self._intent(normalized)
        targets = self._targets(normalized, identities)
        if explicit_api_id:
            targets = unique_strings([explicit_api_id, *targets])
        target = targets[0] if targets else None
        secondary = targets[1] if len(targets) > 1 else None
        filters = self._filters(normalized, catalog)
        if target and intent not in {"duplicates", "capability_groups", "dependency_path", "impact_dependents", "impact_dependencies"}:
            filters.api_ids = [target]
        filters.limit = limit
        payload = filters.model_dump(exclude_none=True, exclude_defaults=True)
        parts = [f"intent={intent}"]
        if target:
            parts.append(f"target={target}")
        if secondary:
            parts.append(f"secondaryTarget={secondary}")
        if payload:
            parts.append("filters=" + json.dumps(payload, sort_keys=True))
        return QueryPlan(intent=intent, target_api_id=target, secondary_api_id=secondary, filters=payload, interpretation="; ".join(parts))

    def _intent(self, question: str) -> str:
        scores: dict[str, int] = defaultdict(int)
        for intent, phrases in INTENTS.items():
            for phrase in phrases:
                token = normalize(phrase)
                if token in question:
                    scores[intent] += len(token.split())
        return max(scores, key=lambda value: scores[value]) if scores else "search"

    def _targets(self, question: str, identities: list[dict[str, Any]]) -> list[str]:
        matches: list[tuple[int, str]] = []
        for item in identities:
            api_id = str(item.get("apiId", ""))
            tokens = [normalize(api_id), normalize(item.get("path", "")), normalize(item.get("normalizedPath", ""))]
            length = max([len(token) for token in tokens if token and token in question] or [0])
            if length:
                matches.append((length, api_id))
        matches.sort(key=lambda value: (-value[0], value[1]))
        return unique_strings(value[1] for value in matches)

    def _filters(self, question: str, catalog: dict[str, list[str]]) -> SearchFilters:
        filters = SearchFilters()
        def matches(key: str) -> list[str]:
            return unique_strings(value for value in catalog.get(key, []) if normalize(value) in question)
        filters.domains = matches("domains")
        filters.capabilities = matches("capabilities")
        filters.entities = matches("entities")
        filters.workflows = matches("workflows")
        filters.workflow_stages = matches("workflowStages")
        filters.actions = matches("actions")
        filters.operation_types = matches("operationTypes")
        filters.consumers = matches("consumers")
        for exposure in ("public", "partner", "internal", "unknown"):
            if exposure in question:
                filters.exposures.append(exposure.title())
        if "high risk" in question:
            filters.min_risk = "High"
        elif "medium risk" in question:
            filters.min_risk = "Medium"
        elif "low risk" in question:
            filters.risk_levels.append("Low")
        if "violations" in question or "violating" in question:
            filters.has_violations = True
        if "no violations" in question:
            filters.has_violations = False
        if "pii" in question:
            filters.contains_pii = True
        if "pci" in question or "cardholder data" in question:
            filters.contains_pci = True
        if "financial data" in question:
            filters.contains_financial_data = True
        if "security sensitive" in question:
            filters.security_sensitive = True
        return filters
