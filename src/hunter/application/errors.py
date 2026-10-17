"""Application errors with stable codes from doc 10 "Ошибки". (S1-03)

Messages are shown to the operator, so they are in Russian and never contain
lead contacts, message bodies, provider payloads or secrets.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    SUPPRESSED = "SUPPRESSED"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SEND_UNRESOLVED = "SEND_UNRESOLVED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ADAPTER_DISABLED = "ADAPTER_DISABLED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class HunterError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
