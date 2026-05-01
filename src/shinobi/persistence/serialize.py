"""Serialisation des modeles pydantic vers/depuis SQLite (JSON ou JSON+zlib)."""

from __future__ import annotations

import json
import zlib
from typing import Any

from pydantic import BaseModel

from shinobi.config import settings


def encode_payload(model: BaseModel) -> bytes:
    """Convertit un modele en bytes (zlib si compression activee)."""
    raw = json.dumps(model.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
    if settings.saves_compress_payloads:
        return zlib.compress(raw, level=6)
    return raw


def decode_payload(data: bytes, cls: type[BaseModel]) -> Any:
    """Reconstruit un modele depuis bytes."""
    if not data:
        raise ValueError("payload vide")
    if settings.saves_compress_payloads:
        try:
            raw = zlib.decompress(data)
        except zlib.error:
            raw = data
    else:
        raw = data
    return cls.model_validate_json(raw)


def encode_json(obj: Any) -> str:
    """Encode JSON sans compression (pour ActionResult, etc.)."""
    return json.dumps(obj, ensure_ascii=False, default=_default)


def _default(o: Any) -> Any:
    if isinstance(o, BaseModel):
        return o.model_dump(mode="json")
    raise TypeError(f"Type non serialisable: {type(o)}")
