"""Research run budget. (H-05)

Doc 09 "Бюджеты и остановка": per run up to 6 source reads, 3 LLM calls (including a format
correction), 20 000 input and 3 000 output tokens, 120 s of active processing, and a money cap.
The next step is checked before it starts by reserving its maximum possible cost; actual usage
is recorded afterwards. Exhausting any limit stops the run with a partial result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

MTOK = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Pricing:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    currency: str

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.input_per_mtok
            + Decimal(output_tokens) * self.output_per_mtok
        ) / MTOK


@dataclass(frozen=True, slots=True)
class Limits:
    max_llm_calls: int = 3
    max_input_tokens: int = 20_000
    max_output_tokens: int = 3_000
    max_active_seconds: float = 120.0
    run_cost_cap: Decimal = Decimal(0)
    daily_cost_cap: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if min(self.max_llm_calls, self.max_input_tokens, self.max_output_tokens) < 1:
            raise ValueError("limits must be positive")
        if self.run_cost_cap < 0 or self.daily_cost_cap < 0:
            raise ValueError("cost caps cannot be negative")


class BudgetExceeded(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class RunBudget:
    limits: Limits
    pricing: Pricing
    spent_today: Decimal = Decimal(0)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = field(default_factory=lambda: Decimal(0))

    def reserve_llm_call(self, estimated_input: int, elapsed_seconds: float) -> int:
        """Check the next call fits; returns the output token cap to request."""
        limits = self.limits
        if elapsed_seconds >= limits.max_active_seconds:
            raise BudgetExceeded("time")
        if self.llm_calls >= limits.max_llm_calls:
            raise BudgetExceeded("llm_calls")
        if self.input_tokens + estimated_input > limits.max_input_tokens:
            raise BudgetExceeded("input_tokens")
        output_cap = limits.max_output_tokens - self.output_tokens
        if output_cap <= 0:
            raise BudgetExceeded("output_tokens")
        worst = self.pricing.cost(estimated_input, output_cap)
        if self.cost + worst > limits.run_cost_cap:
            raise BudgetExceeded("run_cost_cap")
        if self.spent_today + self.cost + worst > limits.daily_cost_cap:
            raise BudgetExceeded("daily_cost_cap")
        return output_cap

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost += self.pricing.cost(input_tokens, output_tokens)


def estimate_tokens(text: str) -> int:
    """ASSUMED ratio: ~3 characters per token for mixed Russian/English text. Conservative for
    Cyrillic; replace with the provider's token counter when one is wired in."""
    return len(text) // 3 + 1
