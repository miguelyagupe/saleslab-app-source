"""Shared exact invariants for the F-02 normalization boundary."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable


def require_finite_decimal(value: object, field: str) -> Decimal:
    """Return a genuine finite Decimal without coercion."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{field} must be a finite Decimal")
    return value


def canonical_decimal(value: Decimal) -> str:
    """Encode a finite Decimal canonically without consulting Decimal context."""
    require_finite_decimal(value, "value")
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    if not any(digits):
        return "0"
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        result = coefficient + "0" * exponent
    else:
        split = len(coefficient) + exponent
        result = (
            coefficient[:split] + "." + coefficient[split:]
            if split > 0
            else "0." + "0" * (-split) + coefficient
        )
    return ("-" if sign else "") + result


def exact_power_of_ten(value: Decimal, exponent: int) -> Decimal:
    """Scale exactly by 10**exponent, independent of active context/traps."""
    require_finite_decimal(value, "value")
    parts = value.as_tuple()
    if exponent < 0:
        raise ValueError("F-02 scaling only supports non-negative powers")
    return Decimal((parts.sign, parts.digits + (0,) * exponent, parts.exponent))


def _integer_and_exponent(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = require_finite_decimal(value, "ratio operand").as_tuple()
    coefficient = int("".join(map(str, digits)) or "0")
    return (-coefficient if sign else coefficient), exponent


def exact_ratio_matches(
    value: Decimal, numerator: Decimal, denominator: Decimal
) -> bool:
    """Compare numerator/denominator*100 with value using integer arithmetic."""
    v, ve = _integer_and_exponent(value)
    n, ne = _integer_and_exponent(numerator)
    d, de = _integer_and_exponent(denominator)
    if d == 0:
        return False
    left, left_exp = n * 100, ne
    right, right_exp = v * d, ve + de
    common = min(left_exp, right_exp)
    return left * 10 ** (left_exp - common) == right * 10 ** (right_exp - common)


def canonical_dimensions(
    dimensions: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Validate and sort dimension pairs for every identity boundary."""
    result = tuple(dimensions)
    keys: list[str] = []
    for pair in result:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError("dimensions must contain key/value pairs")
        key, value = pair
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("dimension keys and values must be strings")
        if not key or not value or key != key.strip() or value != value.strip():
            raise ValueError("dimension keys and values must be non-empty and trimmed")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("dimension keys must be unique")
    return tuple(sorted(result))
