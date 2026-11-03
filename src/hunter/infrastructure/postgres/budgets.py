"""Serialized daily/run reservations, committed before external calls. (S2-07)

Every method owns its transaction. Network calls must happen after it returns. All writers
lock day then run; reservation ids survive worker restarts. No prompts or model text are kept.
"""

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from hunter.domain.budget import BudgetExceeded, Limits, amount


class PostgresBudgets:
    def __init__(self, engine):
        self.engine = engine

    def start(self, run_id, day, now: datetime, limits: Limits):
        if now.utcoffset() is None:
            raise ValueError("budget time must be timezone-aware")
        if day != now.date():
            raise ValueError("day must be the business date of now")
        policy = asdict(limits)
        policy["run_cost"], policy["daily_cost"] = str(limits.run_cost), str(limits.daily_cost)
        state = dict(
            policy=policy,
            started=now.isoformat(),
            calls=0,
            sources=0,
            input=0,
            output=0,
            charged="0",
            actual_input=0,
            actual_output=0,
            pending={},
            finished={},
        )
        with self.engine.begin() as c:
            c.execute(
                text("INSERT INTO budget_days(day,cap) VALUES (:day,:cap) ON CONFLICT DO NOTHING"),
                {"day": day, "cap": limits.daily_cost},
            )
            cap = c.execute(
                text("SELECT cap FROM budget_days WHERE day=:day"), {"day": day}
            ).scalar_one()
            if cap != limits.daily_cost:
                raise ValueError("daily budget policy mismatch")
            c.execute(
                text(
                    "INSERT INTO research_budgets(id,day,state) VALUES (:id,:day,CAST(:s AS jsonb))"
                ),
                dict(id=run_id, day=day, s=json.dumps(state)),
            )

    def _lock(self, c, run_id):
        day = c.execute(
            text("SELECT day FROM research_budgets WHERE id=:id"), {"id": run_id}
        ).scalar_one()
        bank = c.execute(
            text("SELECT charged,provider_floor,cap FROM budget_days WHERE day=:d FOR UPDATE"),
            {"d": day},
        ).one()
        row = c.execute(
            text(
                "SELECT state,stopped_reason,checkpoint FROM research_budgets"
                " WHERE id=:id FOR UPDATE"
            ),
            {"id": run_id},
        ).one()
        return day, bank, row

    def _write(self, c, run_id, state, reason=None, checkpoint=None):
        c.execute(
            text(
                "UPDATE research_budgets SET state=CAST(:s AS jsonb), stopped_reason=:r,"
                " checkpoint=coalesce(CAST(:cp AS jsonb),checkpoint) WHERE id=:id"
            ),
            dict(
                id=run_id,
                s=json.dumps(state),
                r=reason,
                cp=None if checkpoint is None else json.dumps(checkpoint),
            ),
        )

    def reserve(
        self, run_id, call_id, fingerprint, input_tokens, output_tokens, cost, now, checkpoint
    ):
        amount(cost)
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or input_tokens < 0
            or output_tokens < 1
        ):
            raise ValueError("invalid reservation token counts")
        if now.utcoffset() is None:
            raise ValueError("budget time must be timezone-aware")
        reason = None
        with self.engine.begin() as c:
            day, bank, row = self._lock(c, run_id)
            s, p = row.state, row.state["policy"]
            if call_id in s["pending"] or call_id in s["finished"]:
                raise BudgetExceeded("call_already_reserved")
            if row.stopped_reason:
                raise BudgetExceeded(row.stopped_reason)
            if s["pending"]:
                raise BudgetExceeded("pending_call")
            checks = [
                (
                    "day_changed",
                    now.astimezone(datetime.fromisoformat(s["started"]).tzinfo).date() != day,
                ),
                (
                    "time",
                    (now - datetime.fromisoformat(s["started"])).total_seconds() >= p["seconds"],
                ),
                ("llm_calls", s["calls"] >= p["calls"]),
                ("input_tokens", s["input"] + input_tokens > p["input_tokens"]),
                ("output_tokens", s["output"] + output_tokens > p["output_tokens"]),
                ("run_cost", Decimal(s["charged"]) + cost > Decimal(p["run_cost"])),
                (
                    "daily_cost",
                    max(bank.charged, bank.provider_floor) + cost > bank.cap,
                ),
            ]
            reason = next((r for r, fail in checks if fail), None)
            if reason:
                self._write(c, run_id, s, reason, checkpoint)
            else:
                s["calls"] += 1
                s["input"] += input_tokens
                s["output"] += output_tokens
                s["charged"] = str(Decimal(s["charged"]) + cost)
                s["pending"][call_id] = dict(
                    fingerprint=fingerprint,
                    input=input_tokens,
                    output=output_tokens,
                    cost=str(cost),
                )
                c.execute(
                    text("UPDATE budget_days SET charged=charged+:cost WHERE day=:d"),
                    dict(cost=cost, d=day),
                )
                self._write(c, run_id, s, checkpoint=checkpoint)
        if reason:
            raise BudgetExceeded(reason)  # after commit, so checkpoint survives the stop

    def settle(self, run_id, call_id, input_tokens, output_tokens, cost, uncertain=False):
        if cost is not None:
            amount(cost)
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("invalid usage")
        with self.engine.begin() as c:
            day, bank, row = self._lock(c, run_id)
            s = row.state
            if call_id in s["finished"]:
                raise BudgetExceeded("call_already_settled")
            reserved = s["pending"].pop(call_id)
            uncertain = uncertain or cost is None
            charged = max(Decimal(reserved["cost"]), cost or Decimal(0)) if uncertain else cost
            counted_in = max(reserved["input"], input_tokens) if uncertain else input_tokens
            counted_out = max(reserved["output"], output_tokens) if uncertain else output_tokens
            s["input"] += counted_in - reserved["input"]
            s["output"] += counted_out - reserved["output"]
            s["actual_input"] += input_tokens
            s["actual_output"] += output_tokens
            delta = charged - Decimal(reserved["cost"])
            s["charged"] = str(Decimal(s["charged"]) + delta)
            s["finished"][call_id] = dict(
                input=input_tokens,
                output=output_tokens,
                cost=None if cost is None else str(cost),
                uncertain=uncertain,
            )
            c.execute(
                text("UPDATE budget_days SET charged=charged+:delta WHERE day=:d"),
                dict(delta=delta, d=day),
            )
            p = s["policy"]
            exceeded = (
                s["input"] > p["input_tokens"]
                or s["output"] > p["output_tokens"]
                or Decimal(s["charged"]) > Decimal(p["run_cost"])
            )
            reason = row.stopped_reason or (
                "usage_unknown" if uncertain else "actual_over_budget" if exceeded else None
            )
            self._write(c, run_id, s, reason, row.checkpoint)

    def source(self, run_id, now, checkpoint):
        if now.utcoffset() is None:
            raise ValueError("budget time must be timezone-aware")
        reason = None
        with self.engine.begin() as c:
            _, _, row = self._lock(c, run_id)
            s = row.state
            p = s["policy"]
            reason = row.stopped_reason or (
                "time"
                if (now - datetime.fromisoformat(s["started"])).total_seconds() >= p["seconds"]
                else "sources"
                if s["sources"] >= p["sources"]
                else None
            )
            if not reason:
                s["sources"] += 1
            self._write(c, run_id, s, reason, checkpoint)
        if reason:
            raise BudgetExceeded(reason)

    def stop(self, run_id, reason, checkpoint):
        with self.engine.begin() as connection:
            _, _, row = self._lock(connection, run_id)
            self._write(connection, run_id, row.state, row.stopped_reason or reason, checkpoint)

    def reconcile(self, day, provider_spent):
        amount(provider_spent)
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT day FROM budget_days WHERE day=:d FOR UPDATE"), dict(d=day)
            )
            pending = connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM research_budgets WHERE day=:d"
                    " AND state->'pending' <> '{}'::jsonb)"
                ),
                dict(d=day),
            ).scalar_one()
            if pending:
                raise BudgetExceeded("reconciliation_pending")
            connection.execute(
                text(
                    "UPDATE budget_days SET charged=greatest(charged,:v),"
                    " provider_floor=greatest(provider_floor,:v) WHERE day=:d"
                ),
                dict(d=day, v=provider_spent),
            )

    def snapshot(self, run_id):
        with self.engine.connect() as c:
            row = c.execute(
                text("SELECT state,stopped_reason,checkpoint FROM research_budgets WHERE id=:id"),
                dict(id=run_id),
            ).one()
            return dict(row._mapping)
