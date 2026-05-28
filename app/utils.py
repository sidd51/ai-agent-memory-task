from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip().lower())
    return re.sub(r"[^a-z0-9\s]", "", text)


def fingerprint_text(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def safe_json_dumps(payload: dict | list) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str)
