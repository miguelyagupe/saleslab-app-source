from dataclasses import dataclass
from decimal import Decimal, localcontext

from saleslab_v2.domain.normalization import require_finite_decimal


@dataclass(frozen=True)
class RoundingPolicy:
    policy_id: str
    version: str
    quantum: Decimal
    mode: str

    def apply(self, value: Decimal) -> Decimal:
        require_finite_decimal(value, "rounding value")
        with localcontext() as context:
            context.prec = max(100, len(value.as_tuple().digits) + 20)
            return value.quantize(self.quantum, rounding=self.mode)


PERCENT_2DP = RoundingPolicy("percentage_2dp", "1", Decimal("0.01"), "ROUND_HALF_EVEN")
RATIO_2DP = RoundingPolicy("ratio_2dp", "1", Decimal("0.01"), "ROUND_HALF_EVEN")
DAYS_1DP = RoundingPolicy("days_1dp", "1", Decimal("0.1"), "ROUND_HALF_EVEN")
POLICIES = {item.policy_id: item for item in (PERCENT_2DP, RATIO_2DP, DAYS_1DP)}
