"""Research use case: queue, fixed pipeline, budget, validation. (H-05)

Doc 09 "Цикл исследования" in its initial form: a fixed pipeline without free planning by
the model. For each job: claim with a lease -> read evidence -> build context (rules, team
documents, case claims, task, evidence) -> check budget -> call the model -> validate
(research_v1.1) -> at most one format correction -> store the run with usage and cost.
Temporary provider errors are retried inside the same call budget. Exhausting the budget
stops the run with an explicit status (T-19). A worker that lost its lease stores nothing
(T-18). A stored result is scored by code in the same transaction (H-06).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from hunter.application import research_codec
from hunter.application.access import ensure_worker, require, require_mode
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.application.llm_calls import structured_call
from hunter.application.ports import Job, KnowledgeBase, LlmClient
from hunter.application.prompts import (
    PROMPT_VERSION,
    RESEARCH_DOCUMENTS,
    build_research_user,
    build_system,
    evidence_aliases,
    research_schema,
)
from hunter.application.scoring import apply_score
from hunter.domain.budget import Limits, Pricing, RunBudget
from hunter.domain.catalog import Icp, Stage
from hunter.domain.modes import Operation
from hunter.domain.research import SCHEMA_VERSION, Research, parse_research
from hunter.domain.roles import Permission
from hunter.domain.users import User

JOB_KIND = "research"
LEASE = timedelta(minutes=5)  # longer than one run (120 s of active work) and renewed per call
ICP_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    limits: Limits
    pricing: Pricing
    business_tz: timezone


@dataclass(frozen=True, slots=True)
class RunOutcome:
    lead_id: str
    status: str
    error_code: str | None = None


def enqueue_research(deps: Deps, actor: User, *, limit: int, min_prescore: int) -> list[str]:
    """Queue the best NEW leads by pre-score; returns the lead ids queued."""
    require(actor, Permission.WRITE_RESEARCH)
    require_mode(deps, Operation.RESEARCH)
    if not 1 <= limit <= 200:
        raise HunterError(ErrorCode.INVALID_INPUT, "limit: от 1 до 200 за партию")
    with deps.store.transaction():
        lead_ids = deps.store.research_candidates(min_prescore, limit)
        for lead_id in lead_ids:
            job_id = deps.ids.new_id()
            if deps.store.add_job(job_id, JOB_KIND, lead_id, deps.clock.now()):
                deps.audit(actor, "lead", lead_id, "research:queued", reason=f"job {job_id}")
    return lead_ids


def run_research_queue(
    deps: Deps,
    llm: LlmClient,
    knowledge: KnowledgeBase,
    config: ResearchConfig,
    *,
    max_jobs: int,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[RunOutcome]:
    """Process queued research jobs until the queue is empty or max_jobs are done."""
    require_mode(deps, Operation.RESEARCH)
    worker = ensure_worker(deps)
    # Fail before any paid call when the knowledge base is not available.
    documents = knowledge.documents(RESEARCH_DOCUMENTS)
    claims = deps.store.case_claims()
    system = build_system(documents, claims)
    allowed_claims = frozenset(c.id for c in claims if c.usable_in_messages)
    outcomes: list[RunOutcome] = []
    for _ in range(max_jobs):
        token = str(uuid.uuid4())
        with deps.store.transaction():
            now = deps.clock.now()
            job = deps.store.claim_job(JOB_KIND, token, now, now + LEASE)
        if job is None:
            break
        pipeline = _Pipeline(deps, worker, llm, config, system, allowed_claims, sleep, monotonic)
        outcomes.append(pipeline.run(job, token))
    return outcomes


class _Pipeline:
    def __init__(self, deps, worker, llm, config, system, allowed_claims, sleep, monotonic):
        self.deps = deps
        self.worker = worker
        self.llm = llm
        self.config = config
        self.system = system
        self.allowed_claims = allowed_claims
        self.sleep = sleep
        self.monotonic = monotonic

    def run(self, job: Job, token: str) -> RunOutcome:
        store, lead_id = self.deps.store, job.entity_id
        # Resume: a stored result is never recomputed (no second paid call).
        done = store.finished_run_for_job(job.id)
        if done is not None:
            with store.transaction():
                store.finish_job(job.id, token, "done", self.deps.clock.now())
            return RunOutcome(lead_id, done.status, done.error_code)

        lead = store.lead(lead_id)
        if lead is None or lead.stage not in (Stage.NEW, Stage.RESEARCHED):
            with store.transaction():
                store.finish_job(job.id, token, "cancelled", self.deps.clock.now(), "stage")
            return RunOutcome(lead_id, "cancelled", "stage")

        evidence = store.evidence_for(lead.company_id)
        run_id = self.deps.ids.new_id()
        with store.transaction():
            store.add_run(
                run_id,
                lead_id,
                job.id,
                self.llm.model,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                self.config.pricing.currency,
                self.deps.clock.now(),
            )
        budget = RunBudget(self.config.limits, self.config.pricing, spent_today=self._spent_today())
        if not evidence:
            return self._finish(
                job, token, run_id, lead, budget, "insufficient_evidence", "no_evidence"
            )

        user = build_research_user(
            lead_id=lead.id,
            company_id=lead.company_id,
            today=self._today(),
            icp_version=ICP_VERSION,
            evidence=evidence,
            missing_known=[],
        )
        aliases = evidence_aliases(evidence)
        started = self.monotonic()
        outcome = structured_call(
            self.llm,
            system=self.system,
            user=user,
            schema=research_schema(),
            budget=budget,
            validate=lambda data: parse_research(
                data,
                lead_id=lead.id,
                company_id=lead.company_id,
                evidence_aliases=aliases,
                allowed_claim_ids=self.allowed_claims,
                today=self._today(),
            ),
            sleep=self.sleep,
            elapsed=lambda: self.monotonic() - started,
            before_call=lambda: self._heartbeat(job, token, budget),
        )
        if outcome.status == "aborted":
            return RunOutcome(lead_id, "lease_lost", "lease_lost")
        if outcome.status != "ok":
            return self._finish(
                job, token, run_id, lead, budget, outcome.status, outcome.error_code
            )
        research = outcome.value
        status = (
            "insufficient_evidence"
            if research.insufficient
            else ("needs_review" if research.needs_review else "completed")
        )
        return self._finish(job, token, run_id, lead, budget, status, None, research)

    def _finish(
        self,
        job: Job,
        token: str,
        run_id: str,
        lead,
        budget: RunBudget,
        status: str,
        error_code: str | None,
        research: Research | None = None,
    ) -> RunOutcome:
        store, now = self.deps.store, self.deps.clock.now()
        with store.transaction():
            if not store.job_owned(job.id, token):
                return RunOutcome(lead.id, "lease_lost", "lease_lost")
            store.finish_run(
                run_id,
                status=status,
                llm_calls=budget.llm_calls,
                input_tokens=budget.input_tokens,
                output_tokens=budget.output_tokens,
                cost=budget.cost,
                at=now,
                error_code=error_code,
                result=research_codec.dumps(research) if research else None,
                problems=json.dumps(list(research.problems), ensure_ascii=False)
                if research
                else None,
            )
            if research is not None:
                icp = research.icp if research.icp is not Icp.UNKNOWN else lead.icp
                product = research.recommended_product or lead.product
                store.update_lead_research(
                    lead.id,
                    icp=icp.value,
                    product=product.value if product else None,
                    needs_review=research.needs_review,
                    at=now,
                )
                today = self._today()
                apply_score(self.deps, self.worker, store.lead(lead.id), research, run_id, today)
            job_status = "done" if status != "failed" else "failed"
            store.finish_job(job.id, token, job_status, now, error_code)
            self.deps.audit(self.worker, "lead", lead.id, f"research:{status}", reason=error_code)
        return RunOutcome(lead.id, status, error_code)

    def _heartbeat(self, job: Job, token: str, budget: RunBudget) -> bool:
        checkpoint = json.dumps({"step": "llm_call", "calls": budget.llm_calls})
        with self.deps.store.transaction():
            until = self.deps.clock.now() + LEASE
            return self.deps.store.extend_lease(job.id, token, until, checkpoint)

    def _today(self):
        return self.deps.clock.now().astimezone(self.config.business_tz).date()

    def _spent_today(self):
        return spent_today(self.deps, self.config)


def spent_today(deps: Deps, config: ResearchConfig):
    """Model spend since the start of the business day, research and drafts together."""
    local = deps.clock.now().astimezone(config.business_tz)
    day_start = datetime.combine(local.date(), datetime.min.time(), config.business_tz)
    return deps.store.cost_since(day_start, config.pricing.currency)
