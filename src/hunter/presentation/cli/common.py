"""Shared CLI helpers: short ID resolution and output formatting."""

from __future__ import annotations

from hunter.application.errors import ErrorCode, HunterError


def resolve_lead_id(app, prefix: str) -> str:
    """Operators type the first characters of an ID; ambiguity is an error, not a guess."""
    ids = app.store.lead_ids_with_prefix(prefix.strip())
    if not ids:
        raise HunterError(ErrorCode.NOT_FOUND, f"лид {prefix!r} не найден")
    if len(ids) > 1:
        raise HunterError(ErrorCode.INVALID_INPUT, f"ID {prefix!r} неоднозначен, укажите длиннее")
    return ids[0]


def short(value: str | None, limit: int) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
