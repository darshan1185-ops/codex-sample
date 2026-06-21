from __future__ import annotations
from .neo4j_client import Neo4jClient

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT api_id_unique IF NOT EXISTS FOR (n:API) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT domain_name_unique IF NOT EXISTS FOR (n:BusinessDomain) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT capability_name_unique IF NOT EXISTS FOR (n:BusinessCapability) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT workflow_name_unique IF NOT EXISTS FOR (n:Workflow) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT workflow_stage_id_unique IF NOT EXISTS FOR (n:WorkflowStage) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT consumer_name_unique IF NOT EXISTS FOR (n:Consumer) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT data_category_name_unique IF NOT EXISTS FOR (n:DataCategory) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT compliance_name_unique IF NOT EXISTS FOR (n:ComplianceStandard) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT violation_id_unique IF NOT EXISTS FOR (n:Violation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT gateway_name_unique IF NOT EXISTS FOR (n:Gateway) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT team_name_unique IF NOT EXISTS FOR (n:Team) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT environment_name_unique IF NOT EXISTS FOR (n:Environment) REQUIRE n.name IS UNIQUE",
    "CREATE INDEX api_domain_index IF NOT EXISTS FOR (n:API) ON (n.domain)",
    "CREATE INDEX api_capability_index IF NOT EXISTS FOR (n:API) ON (n.businessCapability)",
    "CREATE INDEX api_risk_index IF NOT EXISTS FOR (n:API) ON (n.riskRank)",
    "CREATE INDEX api_exposure_index IF NOT EXISTS FOR (n:API) ON (n.exposure)",
]


async def bootstrap_schema(client: Neo4jClient) -> int:
    for statement in SCHEMA_STATEMENTS:
        await client.write(statement)
    return len(SCHEMA_STATEMENTS)
