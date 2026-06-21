from __future__ import annotations
from typing import Any, Awaitable, Callable
from neo4j import AsyncGraphDatabase, RoutingControl
from .config import Settings


class Neo4jClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            max_connection_pool_size=settings.neo4j_max_connection_pool_size,
            connection_timeout=settings.neo4j_connection_timeout_seconds,
            connection_acquisition_timeout=settings.neo4j_connection_acquisition_timeout_seconds,
            max_transaction_retry_time=settings.neo4j_max_transaction_retry_seconds,
        )

    async def verify_connectivity(self) -> None:
        await self.driver.verify_connectivity()

    async def close(self) -> None:
        await self.driver.close()

    async def read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        records, _, _ = await self.driver.execute_query(
            query,
            parameters_=parameters or {},
            database_=self.settings.neo4j_database,
            routing_=RoutingControl.READ,
        )
        return [record.data() for record in records]

    async def write(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        records, _, _ = await self.driver.execute_query(
            query,
            parameters_=parameters or {},
            database_=self.settings.neo4j_database,
            routing_=RoutingControl.WRITE,
        )
        return [record.data() for record in records]

    async def execute_write(self, work: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        async with self.driver.session(database=self.settings.neo4j_database) as session:
            return await session.execute_write(work, *args)
