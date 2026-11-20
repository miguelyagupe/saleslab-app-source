"""Versioned exact sign application using the frozen locale grammar."""

from decimal import MAX_EMAX, MIN_EMIN, localcontext

from .numbers import LocaleNumberParser


class ExactLocaleNumberParser(LocaleNumberParser):
    version = "locale-number-v3"

    def parse(self, text, **options):
        # The grammar accepts ordinary digit strings, not exponent notation.
        # Their coefficient has no more digits than source characters. Give
        # the legacy sign operation enough precision to retain every digit,
        # independently of the caller's precision, exponent bounds and clamp.
        raw = "" if text is None else str(text)
        with localcontext() as context:
            context.prec = max(1, len(raw) + 1)
            context.Emax = MAX_EMAX
            context.Emin = MIN_EMIN
            context.clamp = 0
            return super().parse(raw, **options)
