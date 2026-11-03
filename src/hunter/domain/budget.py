"""Run policy for T-19; decisions are mechanical, never delegated to the model."""

from dataclasses import dataclass
from decimal import Decimal


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def amount(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("budget amounts must be finite nonnegative Decimals")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class Limits:
    output_tokens: int  # aggregate run cap must be supplied; old 3000 conflicts with 8000/call
    sources: int = 6
    calls: int = 3
    input_tokens: int = 20_000
    seconds: int = 120
    run_cost: Decimal = Decimal("2")
    daily_cost: Decimal = Decimal("200")
    currency: str = "RUB"

    def __post_init__(self):
        for n in (self.sources, self.calls, self.input_tokens, self.output_tokens, self.seconds):
            if type(n) is not int or n < 1:
                raise ValueError("budget limits must be positive integers")
        amount(self.run_cost)
        amount(self.daily_cost)
        if self.currency != "RUB":
            raise ValueError("pilot budget currency is RUB (ADR-0002)")
