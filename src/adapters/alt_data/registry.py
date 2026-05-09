"""
Central registry for alt-data adapters.

Adapters self-register via @register_adapter at module import time. The
generator looks up adapters by source_id; tests iterate REGISTRY.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .base import AltDataAdapter


REGISTRY: dict[str, Type["AltDataAdapter"]] = {}


def register_adapter(cls):
    """Class decorator. Inserts into REGISTRY by source_id.

    Raises ValueError if two classes claim the same source_id; this
    prevents silent shadowing if someone copies an adapter file.
    """
    sid = getattr(cls, "source_id", None)
    if not isinstance(sid, str) or not sid:
        raise ValueError(
            f"@register_adapter: {cls.__name__} must define a non-empty "
            f"class attribute `source_id: str`"
        )
    if sid in REGISTRY and REGISTRY[sid] is not cls:
        raise ValueError(
            f"@register_adapter: source_id={sid!r} already registered "
            f"to {REGISTRY[sid].__name__}; cannot also register {cls.__name__}"
        )
    REGISTRY[sid] = cls
    return cls


def get_adapter(source_id: str) -> Type["AltDataAdapter"]:
    if source_id not in REGISTRY:
        raise KeyError(
            f"Unknown alt-data source_id={source_id!r}. "
            f"Registered: {sorted(REGISTRY)}"
        )
    return REGISTRY[source_id]


def list_adapters() -> list[str]:
    return sorted(REGISTRY)
