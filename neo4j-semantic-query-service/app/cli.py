from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uvicorn

from .config import get_settings
from .neo4j_client import Neo4jClient
from .repository import GraphRepository
from .schema import bootstrap_schema
from .transformer import ProfileTransformer


async def schema_command() -> None:
    settings = get_settings()
    client = Neo4jClient(settings)
    try:
        await client.verify_connectivity()
        count = await bootstrap_schema(client)
        print(json.dumps({"status": "COMPLETED", "statementCount": count}, indent=2))
    finally:
        await client.close()


async def ingest_command(path: Path, source: str, dry_run: bool) -> None:
    settings = get_settings()
    client = Neo4jClient(settings)
    try:
        await client.verify_connectivity()
        await bootstrap_schema(client)
        document = json.loads(path.read_text(encoding="utf-8"))
        repository = GraphRepository(client, settings, ProfileTransformer(settings))
        result = await repository.ingest(document, source=source, dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--workers", type=int, default=1)
    commands.add_parser("schema")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("input", type=Path)
    ingest.add_argument("--source", default="cli")
    ingest.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "serve":
        uvicorn.run("app.main:app", host=args.host or settings.host, port=args.port or settings.port, workers=args.workers)
    elif args.command == "schema":
        asyncio.run(schema_command())
    else:
        asyncio.run(ingest_command(args.input, args.source, args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
