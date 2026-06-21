from __future__ import annotations
import hashlib
import json
import re
from typing import Any, Iterable

RISK_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def normalize(value: Any) -> str:
    text = str(value if value is not None else "")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_./\\-]+", " ", text)
    return " ".join(text.lower().split())


def lower_text(value: Any) -> str:
    return str(value if value is not None else "").strip().lower()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def unique_strings(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        key = normalize(text)
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def first_nonempty(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default


def clean_properties(properties: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in properties.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            output[key] = value
        elif isinstance(value, list):
            output[key] = [item for item in value if isinstance(item, (str, int, float, bool))]
    return output


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def risk_rank(value: Any) -> int:
    return RISK_RANK.get(normalize(value), 0)
