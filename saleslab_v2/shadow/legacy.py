"""Injected legacy adapter. Importing F-09 never imports main or constructs clients."""

import inspect
import json
import math
import re
from copy import deepcopy

from saleslab_v2.domain.idempotency import semantic_hash

from .contracts import LegacyUnavailable


def snapshot_legacy(value):
    """Preserve legacy float representations explicitly; never silently bless them as Decimal."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LegacyUnavailable("nonfinite legacy JSON")
        return {"legacy_float": repr(value)}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, list):
        return [snapshot_legacy(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: snapshot_legacy(v) for k, v in value.items()}
    raise LegacyUnavailable("unsupported legacy output type")


class DeterministicLegacyRunner:
    """Run the actual legacy financial preprocessor with no DB/quota/model effects.

    Inject main.build_deterministic_analysis from an already configured application.
    This is expressly not a full legacy LLM/report evaluation.
    """

    def __init__(self, build_deterministic_analysis):
        self.helper = build_deterministic_analysis
        self._configuration = {
            "role": "legacy_financial",
            "mode": "deterministic_baseline",
            "adapter_version": "legacy-financial-baseline-v1",
            "helper_hash": semantic_hash(
                inspect.getsource(build_deterministic_analysis)
            ),
            "module_hash": semantic_hash(
                inspect.getsource(inspect.getmodule(build_deterministic_analysis))
            ),
        }

    @property
    def configuration(self):
        return deepcopy(self._configuration)

    async def generate(self, shared_input):
        if shared_input["currency"] is None:
            raise LegacyUnavailable(
                "legacy baseline has no representable monetary inputs"
            )
        try:
            result = self.helper(
                "financial", shared_input["currency"], deepcopy(shared_input["fields"])
            )
            return {
                "report": snapshot_legacy(result),
                "usage": None,
                "deterministic_policy": {
                    "forecast_allowed": result["forecast_allowed"]
                },
            }
        except Exception as exc:
            # Store no exception messages, source text, credentials or stack traces.
            raise LegacyUnavailable("legacy deterministic baseline failed") from exc


class LegacyFinancialModelRunner:
    """Evaluate the current legacy prompt/schema/postprocessor with an injected async client.

    The original rows plus derived form fields are sent as shadow input. This
    does not certify the customer upload/parser path or consume product quota.
    """

    def __init__(
        self, legacy_module, client, *, model, max_tokens=6000, timeout_seconds=60
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("explicit legacy model required")
        if (
            type(max_tokens) is not int
            or not 1024 <= max_tokens <= 16384
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 120
        ):
            raise ValueError("invalid legacy model bounds")
        self.module, self.client, self.model = legacy_module, client, model
        self.max_tokens, self.timeout_seconds = max_tokens, timeout_seconds

    @property
    def configuration(self):
        return {
            "role": "legacy_financial",
            "mode": "legacy_model_shadow",
            "provider": "anthropic",
            "model": self.model,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": 0,
            "adapter_version": "legacy-financial-model-shadow-v1",
            "module_hash": semantic_hash(inspect.getsource(self.module)),
            "prompt_factory_hash": semantic_hash(
                inspect.getsource(type(self).generate)
            ),
            "schema_hash": semantic_hash(self.module.REPORT_PROMPTS["financial"]),
        }

    async def generate(self, shared_input):
        try:
            currency = shared_input["currency"]
            if currency is None:
                raise ValueError("legacy requires a monetary currency")
            baseline = self.module.build_deterministic_analysis(
                "financial", currency, deepcopy(shared_input["fields"])
            )
            cfg = self.module.REPORT_PROMPTS["financial"]
            system = f"""You are an {cfg['role']}.
Tone: {cfg['tone']}
Currency: {currency}. Company: the company.
{self.module.FORMATS_NOTICE}
Context: {cfg['context']}
{self.module.deterministic_prompt_block(baseline)}
LANGUAGE: All text values in the JSON MUST be written in European Portuguese (Portugal).
Only keep English for JSON keys and enum values.
If multiple files or periods are provided, compare them directly.
If forecast_allowed is false in deterministic calculations, do not create a forecast; explain missing data instead.
Every recommendation must be specific, supported by evidence, include confidence, effort, urgency, expected_impact and priority_score.
Separate facts present in the data from hypotheses about causes. Do not invent external benchmarks.
Respond ONLY with valid JSON - no markdown, no preamble.
Schema:
{cfg['schema']}
"""
            response = await self.client.with_options(
                timeout=self.timeout_seconds, max_retries=0
            ).messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            shared_input, ensure_ascii=True, sort_keys=True
                        ),
                    }
                ],
            )
            if response.stop_reason != "end_turn":
                raise ValueError("incomplete legacy model response")
            raw = "".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            ).strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            if len(raw.encode()) > 512_000:
                raise ValueError("legacy model response exceeds size limit")
            report = json.loads(raw)
            if not isinstance(report, dict):
                raise TypeError("legacy report must be an object")
            report = self.module.validate_and_enhance_report_result(
                report, "financial", baseline, currency
            )
            usage = getattr(response, "usage", None)
            return {
                "report": snapshot_legacy(report),
                "deterministic_policy": {
                    "forecast_allowed": baseline["forecast_allowed"]
                },
                "usage": (
                    {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                    }
                    if usage
                    else None
                ),
            }
        except Exception as exc:
            raise LegacyUnavailable("legacy model shadow evaluation failed") from exc
