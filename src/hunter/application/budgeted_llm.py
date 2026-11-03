"""T-19: durable reservation before every call, including call_parsed's correction.

Compose as MaskingClient(BudgetedClient(raw_client)): count exactly the masked request the
provider will receive. A calibrated input counter is required; no chars/3 heuristic is used.
BudgetExceeded is handled by the research use case, not disguised as a provider failure.
"""

import hashlib
import json
from dataclasses import replace

from hunter.application.llm import LlmStatus, Usage
from hunter.domain.budget import BudgetExceeded, amount


class BudgetedClient:
    def __init__(self, client, ledger, run_id, *, count_input, clock, checkpoint):
        self.client, self.ledger, self.run_id = client, ledger, run_id
        self.count_input, self.clock, self.checkpoint = count_input, clock, checkpoint

    @property
    def model_id(self):
        return self.client.model_id

    @property
    def tariff(self):
        return self.client.tariff

    def complete_json(self, request):
        tariff = self.tariff
        state = self.ledger.snapshot(self.run_id)["state"]
        policy = state["policy"]
        if tariff is None:
            self._stop("unknown_tariff")
        if tariff.currency != policy["currency"]:
            self._stop("currency")
        amount(tariff.input_per_million)
        amount(tariff.output_per_million)
        if self.count_input is None:
            self._stop("input_counter_unavailable")
        count = self.count_input(request)
        if type(count) is not int or count < 0:
            raise ValueError("input counter must return a nonnegative integer")
        output = min(
            request.max_output_tokens,
            policy["output_per_call"],
            policy["output_tokens"] - state["output"],
        )
        if output < 1:
            self._stop("output_tokens")
        request = replace(request, max_output_tokens=output)
        reserve = amount(tariff.cost(Usage(count, output)))
        fingerprint = hashlib.sha256(
            json.dumps(
                dict(
                    model=self.model_id,
                    system=request.system,
                    user=request.user,
                    schema=request.schema,
                    output=output,
                    prompt_version=request.prompt_version,
                ),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        self.ledger.reserve(
            self.run_id,
            request.call_id,
            fingerprint,
            count,
            output,
            reserve,
            self.clock(),
            self.checkpoint(),
        )
        try:
            result = self.client.complete_json(request)
        except BaseException:
            # The remote side may have billed it. Preserve the reserve, then re-raise bugs
            # and cancellation instead of pretending they were provider errors.
            self.ledger.settle(self.run_id, request.call_id, 0, 0, None, uncertain=True)
            raise
        if result.call_id != request.call_id or result.model_id != self.model_id:
            self.ledger.settle(self.run_id, request.call_id, 0, 0, None, uncertain=True)
            raise ValueError("provider result does not match the reserved call")
        uncertain = result.status is not LlmStatus.SUCCESS or result.currency != tariff.currency
        cost = (
            None
            if result.cost is None or result.currency != tariff.currency
            else max(amount(result.cost), tariff.cost(result.usage))
        )
        self.ledger.settle(
            self.run_id,
            request.call_id,
            result.usage.input_tokens,
            result.usage.output_tokens,
            cost,
            uncertain=uncertain,
        )
        return result

    def _stop(self, reason):
        self.ledger.stop(self.run_id, reason, self.checkpoint())
        raise BudgetExceeded(reason)
