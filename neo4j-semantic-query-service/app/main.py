from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from .config import Settings, get_settings
from .models import DependencyPathRequest, IngestRequest, NaturalLanguageQueryRequest, SearchFilters
from .neo4j_client import Neo4jClient
from .planner import SemanticPlanner
from .repository import GraphRepository
from .schema import bootstrap_schema
from .service import GroundedAnswerBuilder, SemanticService
from .transformer import ProfileTransformer


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = Neo4jClient(resolved)
        try:
            await client.verify_connectivity()
            if resolved.bootstrap_schema_on_startup:
                await bootstrap_schema(client)
        except Exception:
            if resolved.fail_startup_when_neo4j_unavailable:
                await client.close()
                raise
        repository = GraphRepository(client, resolved, ProfileTransformer(resolved))
        app.state.settings = resolved
        app.state.client = client
        app.state.repository = repository
        app.state.service = SemanticService(repository, SemanticPlanner(), GroundedAnswerBuilder(), resolved)
        yield
        await client.close()

    app = FastAPI(title="Neo4j API Semantic Query Service", version=resolved.service_version, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=resolved.cors_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-API-Key", "X-Admin-API-Key"])

    def repo(request: Request) -> GraphRepository:
        return request.app.state.repository

    def client(request: Request) -> Neo4jClient:
        return request.app.state.client

    def service(request: Request) -> SemanticService:
        return request.app.state.service

    async def reader_auth(request: Request, x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None) -> None:
        current: Settings = request.app.state.settings
        if current.api_key is not None and (x_api_key is None or not secrets.compare_digest(x_api_key, current.api_key.get_secret_value())):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")

    async def admin_auth(request: Request, x_admin_api_key: Annotated[str | None, Header(alias="X-Admin-API-Key")] = None) -> None:
        current: Settings = request.app.state.settings
        if current.admin_api_key is None:
            if current.environment.lower() == "production":
                raise HTTPException(status_code=503, detail="ADMIN_API_KEY is not configured.")
            return
        if x_admin_api_key is None or not secrets.compare_digest(x_admin_api_key, current.admin_api_key.get_secret_value()):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-API-Key.")

    Reader = Annotated[None, Depends(reader_auth)]
    Admin = Annotated[None, Depends(admin_auth)]
    Repo = Annotated[GraphRepository, Depends(repo)]
    Client = Annotated[Neo4jClient, Depends(client)]
    Service = Annotated[SemanticService, Depends(service)]

    @app.exception_handler(Neo4jError)
    async def neo4j_error(_: Request, error: Neo4jError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "The semantic graph is temporarily unavailable.", "neo4jCode": getattr(error, "code", None)})

    @app.get("/health/live")
    async def live() -> dict[str, Any]:
        return {"status": "UP", "service": resolved.service_name, "version": resolved.service_version}

    @app.get("/health/ready")
    async def ready(db: Client) -> dict[str, Any]:
        await db.verify_connectivity()
        return {"status": "UP", "neo4j": "reachable"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/catalog")
    async def catalog(_: Reader, repository: Repo) -> dict[str, Any]:
        return {"catalog": await repository.catalog()}

    @app.get("/v1/apis/{api_id}")
    async def get_api(api_id: str, _: Reader, repository: Repo) -> dict[str, Any]:
        profile = await repository.get_api(api_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"API profile not found: {api_id}")
        return profile

    @app.post("/v1/search")
    async def search(body: SearchFilters, _: Reader, repository: Repo) -> dict[str, Any]:
        body.limit = min(body.limit, resolved.maximum_query_limit)
        return {"filters": body.model_dump(exclude_none=True, exclude_defaults=True), "results": await repository.search(body)}

    @app.post("/v1/query")
    async def query(body: NaturalLanguageQueryRequest, _: Reader, semantic_service: Service) -> dict[str, Any]:
        return await semantic_service.query(body)

    @app.post("/v1/query/plan")
    async def plan(body: NaturalLanguageQueryRequest, _: Reader, repository: Repo) -> dict[str, Any]:
        catalog_value, identities = await asyncio.gather(repository.catalog(), repository.identities())
        return SemanticPlanner().plan(body.question, catalog=catalog_value, identities=identities, explicit_api_id=body.api_id, limit=body.limit).model_dump()

    @app.get("/v1/graph/impact/{api_id}")
    async def impact(api_id: str, _: Reader, repository: Repo, direction: Literal["dependencies", "dependents"] = Query(default="dependents"), max_depth: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
        items = await repository.impact(api_id, direction=direction, max_depth=max_depth)
        return {"apiId": api_id, "direction": direction, "results": {"total": len(items), "items": items}}

    @app.post("/v1/graph/path")
    async def graph_path(body: DependencyPathRequest, _: Reader, repository: Repo) -> dict[str, Any]:
        result = await repository.dependency_path(body.from_api_id, body.to_api_id, max_depth=body.max_depth)
        if result is None:
            raise HTTPException(status_code=404, detail="No dependency path was found within the requested depth.")
        return result

    @app.post("/v1/admin/schema/bootstrap")
    async def schema(_: Admin, db: Client) -> dict[str, Any]:
        return {"status": "COMPLETED", "statementCount": await bootstrap_schema(db)}

    @app.post("/v1/admin/ingest")
    async def ingest(body: Annotated[IngestRequest, Body(...)], _: Admin, repository: Repo, semantic_service: Service) -> dict[str, Any]:
        result = await repository.ingest(body.document, source=body.source, dry_run=body.dry_run)
        if not body.dry_run:
            await semantic_service.invalidate_cache()
        return result

    @app.post("/v1/admin/prune")
    async def prune(_: Admin, repository: Repo, semantic_service: Service) -> dict[str, Any]:
        deleted = await repository.prune_orphans()
        await semantic_service.invalidate_cache()
        return {"status": "COMPLETED", "deletedNodes": deleted}

    return app


app = create_app()
