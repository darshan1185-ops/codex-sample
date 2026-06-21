from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    service_name: str = "neo4j-semantic-query-service"
    service_version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    neo4j_uri: str = "neo4j://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("change-me")
    neo4j_database: str = "neo4j"
    neo4j_max_connection_pool_size: int = 50
    neo4j_connection_timeout_seconds: float = 10.0
    neo4j_connection_acquisition_timeout_seconds: float = 30.0
    neo4j_max_transaction_retry_seconds: float = 30.0

    bootstrap_schema_on_startup: bool = True
    fail_startup_when_neo4j_unavailable: bool = True
    api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None
    cors_origins_csv: str = "http://localhost:3000,http://localhost:5173"

    ingestion_batch_size: int = 250
    max_ingestion_profiles: int = 100_000
    max_ingestion_body_bytes: int = 100 * 1024 * 1024
    maximum_query_limit: int = 500
    maximum_dependency_depth: int = 8
    catalog_cache_seconds: int = 60
    store_raw_profile: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_origins_csv.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.environment.lower() != "production":
            return self
        if self.neo4j_password.get_secret_value() == "change-me":
            raise ValueError("NEO4J_PASSWORD must be changed in production.")
        if self.api_key is None:
            raise ValueError("API_KEY is required in production.")
        if self.admin_api_key is None:
            raise ValueError("ADMIN_API_KEY is required in production.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
