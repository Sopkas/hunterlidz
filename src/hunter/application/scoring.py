"""Scoring use cases: score from the latest research, eligibility, operator exception. (H-06)

The score is stored with its rules version on every recalculation (FR-05, FR-14). Eligibility
is computed when asked, from the current contacts and bans, so a new contact ban blocks a lead
immediately without recalculating anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hunter.application import research_codec
from hunter.application.access import require
from hunter.application.context import Deps
from hunter.application.errors import ErrorCode, HunterError
from hunter.domain.catalog import Stage
from hunter.domain.entities import Lead
from hunter.domain.research import Research
from hunter.domain.roles import Permission
from hunter.domain.scoring import Eligibility, Score, compute_score, eligibility
from hunter.domain.users import User


@dataclass(frozen=True, slots=True)
class LeadAssessment:
    lead: Lead
    research: Research | None
    score: Score | None
    eligibility: Eligibility | None


def apply_score(
    deps: Deps, actor: User, lead: Lead, research: Research, run_id: str | None, today: date
) -> Score:
    """Compute and store the score; move NEW -> RESEARCHED when doc 04 conditions hold.
    Must run inside the caller's transaction."""
    store = deps.store
    score = _score(deps, lead, research, today)
    researched = not research.insufficient and bool(research.problem_hypothesis)
    stage = Stage.RESEARCHED if researched and lead.stage is Stage.NEW else lead.stage
    needs_review = research.needs_review or bool(score.contradictions) or not researched
    store.add_score(deps.ids.new_id(), lead.id, run_id, score, deps.clock.now())
    current = store.lead(lead.id)
    if not store.update_lead_score(
        lead.id,
        total=score.total,
        priority=score.priority.value,
        stage=stage.value,
        needs_review=needs_review,
        expected_version=current.version,
        at=deps.clock.now(),
    ):
        raise HunterError(ErrorCode.VERSION_CONFLICT, "карточка обновилась, повторите")
    deps.audit(
        actor,
        "lead",
        lead.id,
        f"score:{score.total}:{score.priority}",
        previous_version=current.version,
        new_version=current.version + 1,
        reason=f"rules {score.rules_version}; stage {stage}",
    )
    return score


def rescore(deps: Deps, actor: User, lead_id: str, today: date) -> Score:
    """Recalculate from the latest research, e.g. after a claim became verified."""
    require(actor, Permission.WRITE_RESEARCH)
    with deps.store.transaction():
        lead = _lead(deps, lead_id)
        run = deps.store.latest_run(lead_id)
        if run is None or not run.result:
            raise HunterError(ErrorCode.MISSING_EVIDENCE, "нет завершённого research")
        return apply_score(deps, actor, lead, research_codec.loads(run.result), run.id, today)


def assess(deps: Deps, lead_id: str) -> LeadAssessment:
    store = deps.store
    lead = _lead(deps, lead_id)
    run = store.latest_run(lead_id)
    research = research_codec.loads(run.result) if run and run.result else None
    score = store.latest_score(lead_id)
    result = None
    if research is not None and score is not None:
        result = eligibility(
            score,
            research,
            contacts=store.contacts_for(lead.company_id),
            suppressed=_suppressed(deps, lead),
            needs_review=lead.needs_review,
            operator_exception=bool(store.contact_exception(lead_id)),
        )
    return LeadAssessment(lead, research, score, result)


def allow_contact(
    deps: Deps, actor: User, lead_id: str, expected_version: int, reason: str
) -> None:
    """Doc 04: a WARM lead (or one whose research needs review) enters the message queue only
    by a recorded operator decision. It never overrides a ban, a missing contact or WATCH/SKIP."""
    require(actor, Permission.EDIT_LEAD)
    if len(reason.strip()) < 10:
        raise HunterError(ErrorCode.INVALID_INPUT, "обоснование исключения: минимум 10 символов")
    with deps.store.transaction():
        view = assess(deps, lead_id)
        if view.eligibility is None:
            raise HunterError(ErrorCode.MISSING_EVIDENCE, "у лида нет score")
        if not view.eligibility.exception_possible:
            blockers = "; ".join(view.eligibility.blockers) or "лид уже в очереди"
            raise HunterError(ErrorCode.INVALID_TRANSITION, f"исключение невозможно: {blockers}")
        if not deps.store.set_contact_exception(
            lead_id, reason.strip(), actor.id, expected_version, deps.clock.now()
        ):
            raise HunterError(ErrorCode.VERSION_CONFLICT, "карточка обновилась, откройте заново")
        deps.audit(
            actor,
            "lead",
            lead_id,
            "contact_exception",
            previous_version=expected_version,
            new_version=expected_version + 1,
            reason=reason.strip(),
        )


def message_queue(deps: Deps, limit: int = 100) -> list[LeadAssessment]:
    views = [assess(deps, i) for i in deps.store.leads_in_stage(Stage.RESEARCHED.value, limit)]
    return [v for v in views if v.eligibility and v.eligibility.eligible]


def _score(deps: Deps, lead: Lead, research: Research, today: date) -> Score:
    store = deps.store
    evidence = store.evidence_for(lead.company_id)
    return compute_score(
        research,
        today=today,
        evidence={e.id: e for e in evidence},
        source_types={s.id: s.source_type for s in store.sources_for(lead.company_id)},
        claims={c.id: c for c in store.case_claims(active_only=False)},
        contacts=store.contacts_for(lead.company_id),
    )


def _suppressed(deps: Deps, lead: Lead) -> bool:
    return deps.store.company_suppressed(lead.company_id)


def _lead(deps: Deps, lead_id: str) -> Lead:
    lead = deps.store.lead(lead_id)
    if lead is None:
        raise HunterError(ErrorCode.NOT_FOUND, "лид не найден")
    return lead
