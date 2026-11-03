"""Budget reservations are committed before calls and survive restarts (T-19)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from hunter.application.budgeted_llm import BudgetedClient
from hunter.application.llm import LlmErrorCode, LlmRequest, LlmResult, Tariff, Usage
from hunter.application.llm_calls import call_parsed
from hunter.domain.budget import BudgetExceeded, Limits
from hunter.domain.research import SchemaError
from hunter.infrastructure.postgres.budgets import PostgresBudgets

NOW = datetime(2026, 11, 3, 10, tzinfo=UTC)
CP = {"step": "research", "completed": ["source:E1"]}
REQUEST = LlmRequest("run:first", "test", "system", "user", {}, 100)
TARIFF = Tariff(Decimal("100"), Decimal("100"), "RUB")


@pytest.fixture
def budget(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM research_budgets"))
        connection.execute(text("DELETE FROM budget_days"))
    ledger = PostgresBudgets(pg_engine)

    def start(**changes):
        run = str(uuid4())
        limits = replace(Limits(output_tokens=8000), **changes)
        ledger.start(run, NOW.date(), NOW, limits)
        return ledger, run

    return start


class Client:
    model_id = "scripted"
    tariff = TARIFF

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def complete_json(self, request):
        self.calls.append(request)
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, LlmErrorCode):
            return LlmResult.failure(request, self.model_id, self.fail, Usage(0, 0), self.tariff)
        return LlmResult.success(request, self.model_id, {"ok": True}, Usage(20, 10), self.tariff)


def wrapper(ledger, run, client=None, **kw):
    args = dict(count_input=lambda r: 100, clock=lambda: NOW, checkpoint=lambda: CP)
    args.update(kw)
    return BudgetedClient(client or Client(), ledger, run, **args)


def reserve(ledger, r, call="first", cost=Decimal(".1"), now=NOW, inputs=100, outputs=100):
    ledger.reserve(r, call, "fingerprint", inputs, outputs, cost, now, CP)


def test_reserve_then_settle_persists_usage_cost_and_checkpoint(budget):
    ledger, r = budget()
    c = wrapper(ledger, r)
    c.complete_json(REQUEST)
    s = ledger.snapshot(r)
    assert s["checkpoint"] == CP and s["state"]["calls"] == 1
    assert (s["state"]["actual_input"], s["state"]["actual_output"]) == (20, 10)
    assert Decimal(s["state"]["charged"]) == Decimal(".003")
    assert not s["state"]["pending"]


@pytest.mark.parametrize(
    "policy,reason",
    [
        ({"run_cost": Decimal("0")}, "run_cost"),
        ({"daily_cost": Decimal("0")}, "daily_cost"),
        ({"input_tokens": 99}, "input_tokens"),
    ],
)
def test_denial_happens_before_remote_call_and_saves_checkpoint(budget, policy, reason):
    ledger, r = budget(**policy)
    base = Client()
    with pytest.raises(BudgetExceeded, match=reason):
        wrapper(ledger, r, base).complete_json(REQUEST)
    assert not base.calls and ledger.snapshot(r)["checkpoint"] == CP
    assert ledger.snapshot(r)["stopped_reason"] == reason


def test_time_expiry_blocks_call(budget):
    ledger, r = budget()
    base = Client()
    with pytest.raises(BudgetExceeded, match="time"):
        wrapper(ledger, r, base, clock=lambda: NOW + timedelta(seconds=120)).complete_json(REQUEST)
    assert not base.calls


def test_call_limit_includes_format_corrections(budget):
    ledger, r = budget(calls=1)
    base = Client()

    def invalid(data):
        raise SchemaError("wrong shape")

    with pytest.raises(BudgetExceeded, match="llm_calls"):
        call_parsed(wrapper(ledger, r, base), REQUEST, invalid)
    assert len(base.calls) == 1 and ledger.snapshot(r)["state"]["calls"] == 1


def test_source_limit_is_checked_before_next_read(budget):
    ledger, r = budget(sources=1)
    ledger.source(r, NOW, CP)
    with pytest.raises(BudgetExceeded, match="sources"):
        ledger.source(r, NOW, CP)
    assert ledger.snapshot(r)["state"]["sources"] == 1 and ledger.snapshot(r)["checkpoint"] == CP


@pytest.mark.parametrize(
    "failure", [LlmErrorCode.TIMEOUT, LlmErrorCode.UNAVAILABLE, RuntimeError("bug")]
)
def test_uncertain_charge_keeps_reserve_and_stops_run(budget, failure):
    ledger, r = budget()
    base = Client(failure)
    c = wrapper(ledger, r, base)
    if isinstance(failure, Exception):
        with pytest.raises(RuntimeError, match="bug"):
            c.complete_json(REQUEST)
    else:
        c.complete_json(REQUEST)
    s = ledger.snapshot(r)
    assert s["stopped_reason"] == "usage_unknown"
    assert Decimal(s["state"]["charged"]) == Decimal(".02")
    with pytest.raises(BudgetExceeded):
        c.complete_json(replace(REQUEST, call_id="retry"))
    assert len(base.calls) == 1


def test_restart_does_not_repeat_pending_or_completed_call(budget, pg_engine):
    ledger, r = budget()
    reserve(ledger, r)
    restarted = PostgresBudgets(pg_engine)
    with pytest.raises(BudgetExceeded, match="call_already_reserved"):
        reserve(restarted, r)
    with pytest.raises(BudgetExceeded, match="pending_call"):
        reserve(restarted, r, "other")
    restarted.settle(r, "first", 10, 10, Decimal(".01"))
    with pytest.raises(BudgetExceeded, match="call_already_reserved"):
        reserve(restarted, r)


def test_provider_statement_can_stop_day_even_when_local_cost_is_small(budget):
    ledger, r = budget()
    ledger.reconcile(NOW.date(), Decimal("200"))
    with pytest.raises(BudgetExceeded, match="daily_cost"):
        reserve(ledger, r)
    ledger.reconcile(NOW.date(), Decimal("0"))  # a stale lower reading does not undo the stop
    _, other = budget()
    with pytest.raises(BudgetExceeded, match="daily_cost"):
        reserve(ledger, other)


def test_unknown_tariff_and_missing_counter_do_not_call_provider(budget):
    ledger, r = budget()
    base = Client()
    base.tariff = None
    with pytest.raises(BudgetExceeded, match="unknown_tariff"):
        wrapper(ledger, r, base).complete_json(REQUEST)
    base.tariff = TARIFF
    with pytest.raises(BudgetExceeded, match="input_counter_unavailable"):
        wrapper(ledger, r, base, count_input=None).complete_json(REQUEST)
    assert not base.calls


def test_output_request_is_clipped_to_remaining_run_tokens(budget):
    ledger, r = budget(output_tokens=50)
    base = Client()
    wrapper(ledger, r, base).complete_json(REQUEST)
    assert base.calls[0].max_output_tokens == 50


def test_actual_over_reserve_is_recorded_and_blocks_later_calls(budget):
    ledger, r = budget(input_tokens=100)
    reserve(ledger, r)
    ledger.settle(r, "first", 101, 10, Decimal(".1"))
    assert ledger.snapshot(r)["stopped_reason"] == "actual_over_budget"
    assert ledger.snapshot(r)["state"]["actual_input"] == 101


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_invalid_money_never_reaches_database(value):
    with pytest.raises(ValueError):
        Limits(output_tokens=8000, run_cost=value)


def test_two_runs_cannot_oversubscribe_daily_cap(budget, pg_engine):
    ledger, r1 = budget(daily_cost=Decimal(".1"))
    _, r2 = budget(daily_cost=Decimal(".1"))
    # A committed outstanding reservation must be visible across independent ledger objects.
    reserve(ledger, r1)
    with pytest.raises(BudgetExceeded, match="daily_cost"):
        reserve(PostgresBudgets(pg_engine), r2)


def test_concurrent_reservations_share_one_daily_cap(budget, pg_engine):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    ledger, one = budget(daily_cost=Decimal(".1"))
    _, two = budget(daily_cost=Decimal(".1"))
    barrier = threading.Barrier(2)

    def attempt(run):
        barrier.wait(timeout=2)
        try:
            reserve(PostgresBudgets(pg_engine), run)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [one, two])) == [False, True]


def test_provider_reconciliation_baseline_includes_future_spend(budget):
    ledger, first = budget(daily_cost=Decimal("1"))
    ledger.reconcile(NOW.date(), Decimal(".8"))
    reserve(ledger, first, cost=Decimal(".1"))
    ledger.settle(first, "first", 1, 1, Decimal(".1"))
    _, second = budget(daily_cost=Decimal("1"))
    reserve(ledger, second, cost=Decimal(".1"))
    ledger.settle(second, "first", 1, 1, Decimal(".1"))
    _, third = budget(daily_cost=Decimal("1"))
    with pytest.raises(BudgetExceeded, match="daily_cost"):
        reserve(ledger, third, cost=Decimal(".01"))


def test_reconciliation_waits_for_pending_calls(budget):
    ledger, run = budget()
    reserve(ledger, run)
    with pytest.raises(BudgetExceeded, match="reconciliation_pending"):
        ledger.reconcile(NOW.date(), Decimal("100"))


def test_new_day_stops_old_run_and_preserves_checkpoint(budget):
    ledger, run = budget()
    with pytest.raises(BudgetExceeded, match="day_changed"):
        reserve(ledger, run, now=NOW + timedelta(days=1))
    assert ledger.snapshot(run)["checkpoint"] == CP
