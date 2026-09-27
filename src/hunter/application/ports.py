"""Interfaces at real boundaries: clock, identifiers and storage. (H-02)

Other boundaries (LLM, source fetching, knowledge base) are added by the tasks that need them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from hunter.domain.cases import CaseClaim
from hunter.domain.contacts import Channel
from hunter.domain.entities import Contact, Evidence, FetchStatus, Lead, Source
from hunter.domain.listings import Listing
from hunter.domain.modes import RunMode
from hunter.domain.users import User


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Normalized outcome of reading one URL; failures carry a safe error code."""

    url: str
    status: FetchStatus
    text: str | None
    content_hash: str | None
    error_code: str | None = None
    truncated: bool = False


class SourceFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult:
        """Read a public page; never raises for network or policy failures."""


class KnowledgeBase(Protocol):
    def documents(self, prefixes: tuple[str, ...]) -> dict[str, str]:
        """Team documents by number prefix ('01', '03'); raises HunterError if one is missing."""


class LlmErrorKind(StrEnum):
    TIMEOUT = "timeout"
    QUOTA = "quota"
    UNAVAILABLE = "unavailable"
    REFUSAL = "refusal"
    AUTH = "auth"
    BAD_REQUEST = "bad_request"


class LlmError(Exception):
    """Normalized provider failure. The message never contains prompts, answers or keys."""

    def __init__(self, kind: LlmErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind.value)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in (LlmErrorKind.TIMEOUT, LlmErrorKind.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class LlmResponse:
    data: object | None  # parsed JSON, None when the text was not valid JSON
    input_tokens: int
    output_tokens: int
    parse_error: str | None = None


class LlmClient(Protocol):
    model: str

    def complete_json(
        self, system: str, user: str, schema: dict, max_output_tokens: int
    ) -> LlmResponse:
        """One structured-output call; raises LlmError for provider failures."""


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    kind: str
    entity_id: str
    attempt: int
    lease_token: str | None
    checkpoint: str | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    lead_id: str
    job_id: str
    status: str
    model_id: str
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost: Decimal
    currency: str
    error_code: str | None
    result: str | None
    problems: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class TaskRow:
    id: str
    lead_id: str
    type: str
    sequence_step: int
    due_at: datetime
    status: str
    note: str | None


@dataclass(frozen=True, slots=True)
class InboundRow:
    id: str
    lead_id: str
    contact_id: str | None
    body: str
    received_at: datetime
    proposal: dict | None
    reply_class: str | None
    action: str | None
    version: int


class Clock(Protocol):
    def now(self) -> datetime:
        """Current moment, timezone-aware UTC."""


class IdGenerator(Protocol):
    def new_id(self) -> str:
        """New unique identifier (UUID string)."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    entity_type: str
    entity_id: str
    action: str
    actor_id: str
    at: datetime
    correlation_id: str
    previous_version: int | None = None
    new_version: int | None = None
    reason: str | None = None


class Store(Protocol):
    def transaction(self) -> AbstractContextManager[None]:
        """Atomic unit of work; nested calls join the outer transaction."""

    # users
    def count_users(self) -> int: ...
    def add_user(self, user: User, at: datetime) -> None: ...
    def user_by_name(self, name: str) -> User | None: ...
    def list_users(self) -> list[User]: ...

    # settings
    def get_mode(self) -> RunMode: ...
    def set_mode(self, mode: RunMode, at: datetime) -> None: ...

    # audit (append-only)
    def add_audit(self, event: AuditEvent) -> None: ...
    def audit_for(self, entity_id: str) -> Iterator[AuditEvent]: ...

    # import (H-03)
    def campaign_id(self, name: str) -> str | None: ...
    def add_campaign(self, campaign_id: str, name: str, icp_version: str, at: datetime) -> None: ...
    def add_import(
        self, import_id: str, campaign_id: str, file_name: str, actor_id: str, at: datetime
    ) -> None: ...
    def add_import_row(
        self, import_id: str, row_no: int, result: str, reason: str | None, lead_id: str | None
    ) -> None: ...
    def listing_by_key(self, source_key: str) -> str | None: ...
    def listing_text_exists(self, text_hash: str) -> bool: ...
    def add_listing(
        self,
        listing_id: str,
        listing: Listing,
        raw_json: str,
        at: datetime,
        screening_version: str,
        **links: object,
    ) -> None: ...
    def company_by_domain(self, domain: str) -> str | None: ...
    def add_company(
        self, company_id: str, name: str | None, domain: str | None, at: datetime
    ) -> None: ...
    def add_source(self, source: Source, at: datetime) -> None: ...
    def sources_for(self, company_id: str) -> list[Source]: ...
    def add_evidence(self, item: Evidence) -> None: ...
    def evidence_for(self, company_id: str) -> list[Evidence]: ...
    def contact_by_address(self, channel: Channel, address: str) -> Contact | None: ...
    def add_contact(self, contact: Contact, at: datetime) -> None: ...
    def contacts_for(self, company_id: str) -> list[Contact]: ...
    def add_lead(
        self, lead: Lead, prescore_json: str, prescore_version: str, at: datetime
    ) -> None: ...
    def active_lead(self, company_id: str, campaign_id: str) -> Lead | None: ...
    def lead(self, lead_id: str) -> Lead | None: ...

    # sources and case claims (H-04)
    def company(self, company_id: str): ...
    def source_by_url(self, source_type: str, url: str) -> Source | None: ...
    def replace_source(self, source: Source, at: datetime) -> None: ...
    def active_case_claim(self, case_key: str) -> CaseClaim | None: ...
    def add_case_claim(self, claim: CaseClaim, at: datetime) -> None: ...
    def case_claims(self, *, active_only: bool = True) -> list[CaseClaim]: ...
    def case_claim(self, claim_id: str) -> CaseClaim | None: ...
