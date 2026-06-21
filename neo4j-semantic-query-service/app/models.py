from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    api_ids: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    path_contains: str | None = None
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    workflow_stages: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    operation_types: list[str] = Field(default_factory=list)
    risk_levels: list[str] = Field(default_factory=list)
    min_risk: str | None = None
    sensitivities: list[str] = Field(default_factory=list)
    criticalities: list[str] = Field(default_factory=list)
    exposures: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    compliance: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)
    violation_controls: list[str] = Field(default_factory=list)
    has_violations: bool | None = None
    contains_pii: bool | None = None
    contains_pci: bool | None = None
    contains_financial_data: bool | None = None
    security_sensitive: bool | None = None
    privileged_operation: bool | None = None
    free_text: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class NaturalLanguageQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    api_id: str | None = None
    limit: int = Field(default=20, ge=1, le=500)
    include_evidence: bool = True


class IngestRequest(BaseModel):
    document: dict[str, Any] | list[dict[str, Any]]
    source: str = Field(default="api", max_length=512)
    dry_run: bool = False


class DependencyPathRequest(BaseModel):
    from_api_id: str = Field(min_length=1)
    to_api_id: str = Field(min_length=1)
    max_depth: int = Field(default=5, ge=1, le=20)


class QueryPlan(BaseModel):
    intent: str
    target_api_id: str | None = None
    secondary_api_id: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    interpretation: str
