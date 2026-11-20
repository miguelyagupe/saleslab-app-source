import hmac
import os, re, json, base64, io, time
from datetime import datetime, timezone
from fastapi import FastAPI, File, UploadFile, Form, Header, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
import anthropic

from database import init_db, get_db
from saleslab_v2.deployment import health_payload
from saleslab_v2.product.api import router as financial_v2_router
from saleslab_v2.product.limits import FinancialUploadLimit
from models import User, Workspace, WorkspaceMember, Analysis, UsageCounter, StripeWebhookEvent
from auth import (
    hash_password, verify_password, verify_google_token,
    issue_session_token, issue_magic_link_token, consume_magic_link_token,
    decode_session_token, get_current_session, require_secret_env,
    ensure_quota_available, consume_quota_after_success, get_remaining_quota, count_workspace_members,
    PLAN_LIMITS,
)

# ── ENV ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ADMIN_SECRET      = require_secret_env("ADMIN_SECRET")
ALLOWED_ORIGIN    = os.environ.get("ALLOWED_ORIGIN", "https://app.saleslab.cc,https://www.saleslab.cc,https://saleslab.cc,https://sales-lab-lovat.vercel.app,https://saleslab-website.vercel.app")
ALLOWED_ORIGINS   = [o.strip() for o in ALLOWED_ORIGIN.split(",") if o.strip()]

if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY not set")

# ── APP ───────────────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="SalesLab API", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(FinancialUploadLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

app.include_router(financial_v2_router)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
MAX_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_EXTS  = {".csv", ".txt", ".xlsx", ".xls", ".pdf"}


@app.on_event("startup")
async def on_startup():
    await init_db()
    from contextlib import asynccontextmanager
    import asyncio
    from database import AsyncSessionLocal
    from saleslab_v2.product.api import get_settings, get_models
    from saleslab_v2.product.jobs import worker_loop
    try:
        enabled = get_settings().financial_product_enabled
    except ValueError:
        enabled = False
    if enabled:
        app.state.financial_worker = asyncio.create_task(worker_loop(AsyncSessionLocal, get_settings, asynccontextmanager(get_models)))


@app.on_event("shutdown")
async def shutdown_financial_worker():
    import asyncio
    worker = getattr(app.state, "financial_worker", None)
    if worker is not None:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


def sanitize(text: str, max_len: int) -> str:
    return (text or "").strip()[:max_len]


def append_if_present(parts: list[str], label: str, value: str, max_len: int = 4000):
    value = sanitize(value, max_len)
    if value:
        parts.append(f"{label}: {value}")


def build_manual_input_parts(
    *,
    report_type: str,
    currency: str,
    period_current: str = "",
    period_previous: str = "",
    revenue_current: str = "",
    revenue_previous: str = "",
    units_current: str = "",
    units_previous: str = "",
    top_products: str = "",
    top_clients: str = "",
    top_salespeople: str = "",
    op_costs: str = "",
    extra_notes: str = "",
    team_data: str = "",
    output_metric: str = "",
    total_labor_cost: str = "",
    total_costs: str = "",
    cogs: str = "",
    opex: str = "",
    net_result: str = "",
) -> list[str]:
    """Build the user-facing manual input block, preserving legacy fields and module-specific fields."""
    parts: list[str] = []
    append_if_present(parts, "Período actual", period_current, 200)
    append_if_present(parts, "Período anterior", period_previous, 200)

    if report_type in {"sales", "cost", "financial"}:
        append_if_present(parts, f"Receita/Volume actual ({currency})", revenue_current, 200)
        append_if_present(parts, f"Receita/Volume anterior ({currency})", revenue_previous, 200)

    if report_type == "sales":
        append_if_present(parts, "Output/Unidades actual", units_current, 200)
        append_if_present(parts, "Output/Unidades anterior", units_previous, 200)
        append_if_present(parts, "Top produtos/serviços", top_products, 1000)
        append_if_present(parts, "Top clientes/unidades", top_clients, 1000)
        append_if_present(parts, "Top colaboradores/equipas", top_salespeople, 1000)

    if report_type == "efficiency":
        append_if_present(parts, "Dados por equipa ou colaborador", team_data, 4000)
        append_if_present(parts, "Métrica de output", output_metric, 200)
        append_if_present(parts, f"Custo total de pessoal ({currency})", total_labor_cost, 200)

    if report_type == "cost":
        append_if_present(parts, "Custos operacionais", op_costs, 4000)
        append_if_present(parts, f"Total de custos ({currency})", total_costs, 200)

    if report_type == "financial":
        append_if_present(parts, f"COGS / custo dos bens ou serviços ({currency})", cogs, 200)
        append_if_present(parts, f"Despesas operacionais ({currency})", opex, 200)
        append_if_present(parts, f"Resultado líquido ({currency})", net_result, 200)

    append_if_present(parts, "Notas", extra_notes, 2000)
    return parts


def parse_numeric_value(value: Any) -> float | None:
    """Parse common human-entered money/percentage/quantity strings without guessing absent values."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[€$£R]|EUR|USD|GBP|BRL", "", text, flags=re.I)
    text = text.replace("\u00a0", " ").replace(" ", "")
    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        text = text.replace(".", "").replace(",", ".")
    elif has_comma:
        text = text.replace(",", ".")
    elif has_dot:
        parts = text.split(".")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts if p):
            text = text.replace(".", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None



def parse_named_amounts(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for chunk in re.split(r"[\n;]+", sanitize(raw, 5000)):
        for part in re.split(r",(?=\s*[^,]+(?:€|eur|\d))", chunk, flags=re.I):
            text = part.strip(" -•\t")
            if not text:
                continue
            amount_match = re.search(r"(?:€\s*)?(-?\d[\d .]*(?:[,.]\d+)?)\s*(?:€|eur)?", text, re.I)
            if not amount_match:
                continue
            amount = parse_numeric_value(amount_match.group(1))
            if amount is None:
                continue
            name = re.sub(r"(?:€|eur)\s*$", "", (text[:amount_match.start()] + text[amount_match.end():]).strip(" :-–—"), flags=re.I).strip()
            items.append({"name": re.sub(r"\s+", " ", name)[:80] or text[:80], "amount": amount, "raw": text[:160]})
    return items


def consistency_tolerance(total: float) -> float:
    return max(1.0, abs(total) * 0.01)


def component_sum_issue(*, total_label: str, total: float | None, component_label: str, components: list[dict[str, Any]], currency: str) -> dict[str, Any] | None:
    if total is None or total < 0 or not components:
        return None
    observed = sum(item["amount"] for item in components)
    difference = observed - total
    tolerance = consistency_tolerance(total)
    if difference <= tolerance:
        return None
    relative_difference = (difference / total) * 100 if total else 100
    severity = "critical" if relative_difference >= 25 else "warning"
    component_names = ", ".join(item["name"] for item in components[:5])
    return {
        "type": "component_sum_exceeds_total",
        "severity": severity,
        "evidence": f"{component_label} ({component_names}) somam {format_amount(observed, currency)}, acima de {total_label} {format_amount(total, currency)}.",
        "expected": f"Soma de {component_label} <= {format_amount(total + tolerance, currency)} incluindo tolerância de arredondamento.",
        "observed": format_amount(observed, currency),
        "difference": format_amount(difference, currency),
        "confidence": "high",
    }

def format_amount(value: float | None, currency: str = "EUR", suffix: str = "") -> str:
    if value is None:
        return ""
    rounded = round(value, 2)
    if rounded.is_integer():
        rendered = f"{int(rounded):,}".replace(",", ".")
    else:
        rendered = f"{rounded:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefix = "€" if currency == "EUR" else f"{currency} "
    return f"{prefix}{rendered}{suffix}"


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / abs(previous)) * 100


def pct_label(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:+.1f}%".replace(".", ",")


def numeric_kpi(metric_id: str, label: str, value: str, change: str = "", trend: str = "neutral") -> dict[str, str]:
    return {"metric_id": metric_id, "label": label, "value": value, "change": change, "trend": trend}


def calculate_impact_score(impact_amount: float | None, impact_base_amount: float | None = None) -> int:
    if impact_amount in (None, 0):
        return 0
    if impact_base_amount is not None and impact_base_amount > 0:
        relative_pct = (abs(impact_amount) / impact_base_amount) * 100
        return min(100, max(1, round(relative_pct * 5)))
    return min(100, max(10, int(abs(impact_amount) / 1000 * 10)))


def build_recommendation(
    *,
    title: str,
    why: str,
    evidence: list[str],
    action: str,
    impact_amount: float | None = None,
    impact_base_amount: float | None = None,
    currency: str = "EUR",
    urgency: str = "medium",
    confidence: str = "medium",
    effort: str = "medium",
) -> dict[str, Any]:
    # priority_score formula (0-100 scale):
    #   0.35 * impact_score + 0.25 * urgency_score + 0.25 * confidence_score + 0.15 * ease_score.
    # impact_score is relative when a justified denominator exists: abs(impact_amount) / impact_base_amount
    # as a percentage, multiplied by 5 and capped at 100 (20%+ relative impact => 100). This is used
    # for sales recommendations against revenue and cost recommendations against total costs. Without a
    # positive denominator, fallback is conservative absolute scoring: 10 points per 1,000 units, capped
    # to 10..100. Zero or missing impact is always 0. Urgency/confidence map high/medium/low to 90/60/30;
    # ease maps effort low/medium/high to 90/60/30, so lower effort ranks higher.
    impact_score = calculate_impact_score(impact_amount, impact_base_amount)
    urgency_score = {"high": 90, "medium": 60, "low": 30}.get(urgency, 60)
    confidence_score = {"high": 90, "medium": 60, "low": 30}.get(confidence, 60)
    ease_score = {"low": 90, "medium": 60, "high": 30}.get(effort, 60)
    priority_score = round((0.35 * impact_score) + (0.25 * urgency_score) + (0.25 * confidence_score) + (0.15 * ease_score))
    return {
        "title": title,
        "why": why,
        "evidence": evidence,
        "recommended_action": action,
        "expected_impact": format_amount(impact_amount, currency) if impact_amount is not None else "Impacto não quantificado",
        "impact_amount": impact_amount,
        "urgency": urgency,
        "confidence": confidence,
        "effort": effort,
        "priority_score": priority_score,
    }


def parse_efficiency_rows(team_data: str) -> list[dict[str, Any]]:
    rows = []
    for line in sanitize(team_data, 4000).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, details = line.split(":", 1)
        hours_match = re.search(r"(\d+(?:[,.]\d+)?)\s*h", details, re.I)
        cost_match = re.search(r"(?:custo|€|eur)\s*([€\s\d.,]+)", details, re.I)
        numbers = [parse_numeric_value(n) for n in re.findall(r"-?\d+(?:[,.]\d+)?", details)]
        hours = parse_numeric_value(hours_match.group(1)) if hours_match else None
        cost = parse_numeric_value(cost_match.group(1)) if cost_match else None
        output_candidates = [n for n in numbers if n is not None and n != hours and n != cost]
        output = output_candidates[0] if output_candidates else None
        if name.strip() and (hours or output or cost):
            rows.append({"unit": name.strip(), "hours": hours, "output": output, "cost": cost})
    return rows


def build_deterministic_analysis(report_type: str, currency: str, fields: dict[str, str]) -> dict[str, Any]:
    """Small deterministic layer for facts/calculations; interpretation remains with the model."""
    metrics: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    evidence: list[str] = []
    data_quality_issues: list[dict[str, Any]] = []
    forecast_allowed = False
    forecast_reason = "Histórico insuficiente para previsão: são necessários pelo menos período actual e anterior com métrica comparável."

    revenue_current = parse_numeric_value(fields.get("revenue_current"))
    revenue_previous = parse_numeric_value(fields.get("revenue_previous"))
    units_current = parse_numeric_value(fields.get("units_current"))
    units_previous = parse_numeric_value(fields.get("units_previous"))
    revenue_change = pct_change(revenue_current, revenue_previous)

    if revenue_current is not None:
        metrics.append(numeric_kpi("revenue", "Receita", format_amount(revenue_current, currency), pct_label(revenue_change), "up" if (revenue_change or 0) > 0 else "down" if (revenue_change or 0) < 0 else "neutral"))
        evidence.append(f"Receita actual fornecida: {format_amount(revenue_current, currency)}")
    if revenue_previous is not None:
        evidence.append(f"Receita anterior fornecida: {format_amount(revenue_previous, currency)}")
    if revenue_current is not None and revenue_previous not in (None, 0):
        forecast_allowed = True
        forecast_reason = "Existe pelo menos receita actual e anterior; permitir apenas projeção simples, sem alegar sazonalidade."
        delta = revenue_current - revenue_previous
        if abs(delta) > 0:
            recommendations.append(build_recommendation(
                title="Investigar a variação de receita",
                why=f"A receita variou {pct_label(revenue_change)} face ao período anterior.",
                evidence=[f"{format_amount(revenue_previous, currency)} → {format_amount(revenue_current, currency)}"],
                action="Identificar quais clientes, produtos ou canais explicam a variação antes de alterar investimento comercial.",
                impact_amount=abs(delta),
                impact_base_amount=abs(revenue_previous),
                currency=currency,
                urgency="high" if delta < 0 else "medium",
                confidence="high",
                effort="medium",
            ))

    if report_type == "sales":
        unit_change = pct_change(units_current, units_previous)
        if units_current is not None:
            metrics.append(numeric_kpi("units_sold", "Unidades vendidas", f"{int(units_current)}", pct_label(unit_change), "up" if (unit_change or 0) > 0 else "down" if (unit_change or 0) < 0 else "neutral"))
        if revenue_current is not None and units_current not in (None, 0):
            avg_ticket = revenue_current / units_current
            metrics.append(numeric_kpi("avg_revenue_per_unit", "Receita média por unidade", format_amount(avg_ticket, currency), "", "neutral"))
        for field_name, label in (("top_clients", "clientes indicados"), ("top_products", "produtos/serviços indicados")):
            issue = component_sum_issue(
                total_label="receita total",
                total=revenue_current,
                component_label=label,
                components=parse_named_amounts(fields.get(field_name, "")),
                currency=currency,
            )
            if issue:
                data_quality_issues.append(issue)

    if report_type == "efficiency":
        rows = parse_efficiency_rows(fields.get("team_data", ""))
        ratios = []
        for row in rows:
            if row.get("output") is not None and row.get("hours") not in (None, 0):
                row["output_per_hour"] = row["output"] / row["hours"]
                ratios.append(row["output_per_hour"])
            if row.get("cost") is not None and row.get("output") not in (None, 0):
                row["cost_per_output"] = row["cost"] / row["output"]
        if ratios:
            avg_ratio = sum(ratios) / len(ratios)
            worst = min((r for r in rows if r.get("output_per_hour") is not None), key=lambda r: r["output_per_hour"])
            best = max((r for r in rows if r.get("output_per_hour") is not None), key=lambda r: r["output_per_hour"])
            metrics.append(numeric_kpi("avg_output_per_hour", "Output médio por hora", f"{avg_ratio:.2f}", "", "neutral"))
            evidence.append(f"Melhor rácio: {best['unit']} ({best['output_per_hour']:.2f}/h); pior rácio: {worst['unit']} ({worst['output_per_hour']:.2f}/h).")
            gain_units = max(0, (avg_ratio - worst["output_per_hour"]) * (worst.get("hours") or 0))
            recommendations.append(build_recommendation(
                title=f"Analisar produtividade de {worst['unit']}",
                why=f"{worst['unit']} está abaixo da média de output por hora.",
                evidence=[f"{worst['unit']}: {worst['output_per_hour']:.2f}/h", f"Média: {avg_ratio:.2f}/h"],
                action="Comparar processo, mix de tarefas, equipamento e carga de trabalho antes de concluir causa.",
                impact_amount=gain_units,
                currency="unidades",
                urgency="medium",
                confidence="high",
                effort="medium",
            ))

    if report_type == "cost":
        explicit_total_costs = parse_numeric_value(fields.get("total_costs"))
        total_costs = explicit_total_costs or parse_numeric_value(fields.get("op_costs"))
        if total_costs is not None:
            metrics.append(numeric_kpi("total_costs", "Total de custos", format_amount(total_costs, currency), "", "neutral"))
            issue = component_sum_issue(
                total_label="total de custos",
                total=explicit_total_costs,
                component_label="categorias de custo indicadas",
                components=parse_named_amounts(fields.get("op_costs", "")),
                currency=currency,
            )
            if issue:
                data_quality_issues.append(issue)
            if revenue_current:
                cost_ratio = (total_costs / revenue_current) * 100
                metrics.append(numeric_kpi("costs_pct_revenue", "Custos / Receita", f"{cost_ratio:.1f}%".replace(".", ","), "", "down" if cost_ratio > 80 else "neutral"))
                evidence.append(f"Custos representam {cost_ratio:.1f}% da receita.")
                recommendations.append(build_recommendation(
                    title="Priorizar categorias de custo com maior peso na receita",
                    why="O impacto deve ser medido por peso na receita e variação face ao período anterior, não por benchmark externo sem fonte.",
                    evidence=[f"Custos totais: {format_amount(total_costs, currency)}", f"Receita: {format_amount(revenue_current, currency)}"],
                    action="Ordenar categorias por valor absoluto e variação antes de cortar custos.",
                    impact_amount=total_costs * 0.05,
                    impact_base_amount=total_costs,
                    currency=currency,
                    urgency="high" if cost_ratio > 80 else "medium",
                    confidence="medium",
                    effort="medium",
                ))

    if report_type == "financial":
        cogs = parse_numeric_value(fields.get("cogs"))
        opex = parse_numeric_value(fields.get("opex"))
        net_result = parse_numeric_value(fields.get("net_result"))
        if revenue_current is not None and cogs is not None:
            gross_profit = revenue_current - cogs
            gross_margin = (gross_profit / revenue_current) * 100 if revenue_current else None
            metrics.append(numeric_kpi("gross_profit", "Margem bruta", format_amount(gross_profit, currency), "", "neutral"))
            metrics.append(numeric_kpi("gross_margin_pct", "Margem bruta %", f"{gross_margin:.1f}%".replace(".", ","), "", "neutral"))
            evidence.append(f"Margem bruta calculada: {format_amount(revenue_current, currency)} - {format_amount(cogs, currency)} = {format_amount(gross_profit, currency)}.")
        if revenue_current is not None and opex is not None:
            opex_ratio = (opex / revenue_current) * 100 if revenue_current else None
            metrics.append(numeric_kpi("opex_pct_revenue", "OPEX / Receita", f"{opex_ratio:.1f}%".replace(".", ","), "", "neutral"))
        if revenue_current is not None and net_result is not None:
            net_margin = (net_result / revenue_current) * 100 if revenue_current else None
            metrics.append(numeric_kpi("net_margin_pct", "Margem líquida", f"{net_margin:.1f}%".replace(".", ","), "", "up" if net_margin and net_margin > 0 else "down"))

    if data_quality_issues:
        forecast_allowed = False
        forecast_reason = "Dados contraditórios detectados; validar agregados e componentes antes de criar previsão."
        evidence.extend(issue["evidence"] for issue in data_quality_issues)
        for recommendation in recommendations:
            if recommendation.get("confidence") == "high":
                recommendation["confidence"] = "medium"

    return {
        "metrics": metrics,
        "recommendations": recommendations,
        "evidence": evidence,
        "data_quality_issues": data_quality_issues,
        "forecast_allowed": forecast_allowed,
        "forecast_reason": forecast_reason,
    }


def deterministic_prompt_block(analysis: dict[str, Any]) -> str:
    if not analysis["metrics"] and not analysis["evidence"] and not analysis["recommendations"]:
        return ""
    return "\n".join([
        "DETERMINISTIC FACTS AND CALCULATIONS:",
        json.dumps(analysis, ensure_ascii=False, indent=2),
        "Use these calculations as factual anchors. Do not contradict them. If data_quality_issues is not empty, explicitly mention the inconsistency, do not decide which value is correct, and treat affected conclusions as uncertain hypotheses.",
    ])


def normalize_recommendations(result: dict[str, Any], deterministic: dict[str, Any], currency: str) -> list[dict[str, Any]]:
    normalized = [r for r in result.get("recommendations", []) if isinstance(r, dict)]
    normalized.extend(deterministic.get("recommendations", []))
    if not normalized:
        for alert in result.get("alerts", [])[:3] if isinstance(result.get("alerts"), list) else []:
            message = alert.get("message") if isinstance(alert, dict) else str(alert)
            normalized.append(build_recommendation(
                title=message[:90],
                why=message,
                evidence=deterministic.get("evidence", [])[:2],
                action="Validar este alerta nos dados de origem e definir uma ação concreta antes da próxima análise.",
                currency=currency,
                urgency="high" if isinstance(alert, dict) and alert.get("level") == "critical" else "medium",
                confidence="low" if not deterministic.get("evidence") else "medium",
            ))
    for rec in normalized:
        rec.setdefault("evidence", deterministic.get("evidence", [])[:2])
        rec.setdefault("expected_impact", "Impacto não quantificado")
        rec.setdefault("confidence", "medium" if rec.get("evidence") else "low")
        rec.setdefault("effort", "medium")
        rec.setdefault("urgency", "medium")
        rec.setdefault("priority_score", 50)
        rec.setdefault("recommended_action", rec.get("action") or rec.get("next_step") or "Definir próximo passo operacional.")
    return sorted(normalized, key=lambda r: r.get("priority_score", 0), reverse=True)


def validate_and_enhance_report_result(result: dict[str, Any], report_type: str, deterministic: dict[str, Any], currency: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="Resultado da análise inválido.")
    result["report_type"] = report_type
    result.setdefault("kpis", [])
    result.setdefault("alerts", [])
    result.setdefault("missing_data", [])
    result.setdefault("data_quality", "partial")
    issues = deterministic.get("data_quality_issues", []) or []
    if issues:
        result["data_quality_issues"] = issues
        if result.get("data_quality") == "good":
            result["data_quality"] = "partial"
        existing_alerts = result.get("alerts", []) if isinstance(result.get("alerts"), list) else []
        for issue in issues:
            existing_alerts.insert(0, {
                "level": "critical" if issue.get("severity") == "critical" else "warning",
                "message": f"Inconsistência nos dados: {issue.get('evidence')}",
            })
        result["alerts"] = existing_alerts

    existing_metric_ids = {k.get("metric_id") for k in result.get("kpis", []) if isinstance(k, dict)}
    for metric in deterministic.get("metrics", []):
        if metric["metric_id"] not in existing_metric_ids:
            result["kpis"].insert(0, metric)
            existing_metric_ids.add(metric["metric_id"])

    if not deterministic.get("forecast_allowed") and result.get("forecast"):
        result["forecast"] = {
            "next_period": None,
            "confidence": "low",
            "rationale": deterministic.get("forecast_reason"),
            "actions": [],
            "suppressed": True,
        }
        if "forecast_basis" not in result.get("missing_data", []):
            result["missing_data"].append("forecast_basis")

    result["recommendations"] = normalize_recommendations(result, deterministic, currency)
    if issues:
        for recommendation in result["recommendations"]:
            if recommendation.get("confidence") == "high":
                recommendation["confidence"] = "medium"
    result["evidence"] = list(dict.fromkeys((result.get("evidence") or []) + deterministic.get("evidence", [])))
    return result

# ── FILE PROCESSING ───────────────────────────────────────────────────────────

async def process_file(file: UploadFile):
    """Returns (text_content, pdf_bytes) — one will be None."""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"Ficheiro demasiado grande (máx. 5MB)")
    ext = os.path.splitext(file.filename or "")[-1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Formato não suportado: {ext}")
    if ext == ".pdf":
        return None, content
    elif ext in (".xlsx", ".xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            rows = []
            for ws in wb.worksheets:
                rows.append(f"[Folha: {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
            return "\n".join(rows[:400]), None
        except Exception:
            raise HTTPException(status_code=400, detail=f"Não foi possível processar {file.filename}")
    else:
        return content.decode("utf-8", errors="replace")[:15000], None

# ── REPORT PROMPTS (inalterado) ───────────────────────────────────────────────

FORMATS_NOTICE = """DATA FORMATS — you accept all of these without exception:
- Excel or CSV files (from any software: ERP, CRM, accounting, custom sheets)
- PDF reports (management reports, P&L statements, invoicing summaries)
- Text pasted from any source (CRM dashboards, email reports, WhatsApp messages)
- Free-form descriptions with numbers embedded in prose
- Partial data — always analyse what is available; note clearly what is missing
- Mixed languages (Portuguese, English, Spanish — handle all)

CRITICAL RULE: Never refuse to analyse because the format is unusual or data is incomplete.
Extract whatever is present. Estimate or flag gaps. Always produce a useful output."""

REPORT_PROMPTS = {
    "sales": {
        "role": "elite sales analyst producing internal management reports",
        "tone": "direct, data-driven, commercially sharp. No fluff. Focus on what matters.",
        "context": "Companies submit data in any format they already have — CRM exports, Excel sheets, pasted tables, PDF invoicing reports, or free-form text. Extract whatever is available.",
        "schema": """{
  "report_type": "sales",
  "period": "<period label>",
  "executive_summary": "<3-5 sentence sharp executive summary>",
  "kpis": [{"label": "<KPI name>", "value": "<formatted value>", "change": "<% or absolute change>", "trend": "up|down|neutral"}],
  "top_products": [{"rank": 1, "name": "<n>", "value": "<revenue or units>", "note": "<insight>"}],
  "top_clients": [{"rank": 1, "name": "<n>", "value": "<revenue>", "note": "<insight>"}],
  "top_salespeople": [{"rank": 1, "name": "<n>", "value": "<revenue or deals>", "note": "<insight>"}],
  "alerts": [{"level": "critical|warning|positive", "message": "<concise alert>"}],
  "recommendations": [{"title": "<decision>", "why": "<why this matters>", "evidence": ["<supporting data>"], "recommended_action": "<specific next step>", "expected_impact": "<amount or not quantified>", "urgency": "high|medium|low", "confidence": "high|medium|low", "effort": "low|medium|high", "priority_score": 0}],
  "forecast": {
    "next_period": "<label>",
    "revenue_estimate": "<value with currency>",
    "confidence": "high|medium|low",
    "rationale": "<2-3 sentences>",
    "actions": ["<action 1>", "<action 2>"]
  },
  "comparison_narrative": "<2-3 sentences comparing periods>",
  "data_quality": "good|partial|insufficient",
  "missing_data": ["<field missing if any>"],
  "evidence": ["<factual support for the most important insights>"]
}"""
    },
    "efficiency": {
        "role": "operations efficiency analyst specialising in workforce and process optimisation",
        "tone": "analytical, precise, focused on output-per-cost ratios. No fluff.",
        "context": "Companies submit any operational data they track — hours worked, units produced, team sheets, shift logs, productivity reports from any system.",
        "schema": """{
  "report_type": "efficiency",
  "period": "<period label>",
  "executive_summary": "<3-5 sentence summary of operational efficiency>",
  "kpis": [{"label": "<KPI name>", "value": "<formatted value>", "change": "<% or absolute change>", "trend": "up|down|neutral"}],
  "team_performance": [
    {
      "unit": "<team / employee / location name>",
      "output": "<what they produce>",
      "cost": "<labour cost or hours>",
      "efficiency_ratio": "<output per cost unit>",
      "vs_average": "above|below|at",
      "note": "<insight or recommendation>"
    }
  ],
  "bottlenecks": [{"area": "<process or team>", "issue": "<cause>", "impact": "<cost or time lost>", "recommendation": "<action>"}],
  "alerts": [{"level": "critical|warning|positive", "message": "<concise alert>"}],
  "recommendations": [{"title": "<decision>", "why": "<why this matters>", "evidence": ["<supporting data>"], "recommended_action": "<specific next step>", "expected_impact": "<amount or not quantified>", "urgency": "high|medium|low", "confidence": "high|medium|low", "effort": "low|medium|high", "priority_score": 0}],
  "optimisation_actions": [{"priority": "high|medium|low", "action": "<specific action>", "expected_gain": "<saving or improvement in euros>"}],
  "summary_narrative": "<2-3 sentences on overall efficiency health>",
  "data_quality": "good|partial|insufficient",
  "missing_data": ["<field missing if any>"],
  "evidence": ["<factual support for the most important insights>"]
}"""
    },
    "cost": {
        "role": "cost reduction analyst specialising in identifying unnecessary expenditure",
        "tone": "direct, commercially ruthless, focused on margin protection. No fluff.",
        "context": "Companies submit any cost data they have — bank statements, accounting exports, invoices by category, expense sheets from any software (Primavera, SAP, Sage, PHC, Excel).",
        "schema": """{
  "report_type": "cost",
  "period": "<period label>",
  "executive_summary": "<3-5 sentence summary of cost health>",
  "total_costs": "<total expenditure>",
  "cost_breakdown": [{"category": "<category>", "amount": "<value>", "pct_of_total": "<%>", "vs_previous": "<change>", "status": "ok|review|critical"}],
  "unnecessary_costs": [{"item": "<cost item>", "current_spend": "<amount>", "benchmark": "<what it should be>", "excess": "<waste amount>", "action": "<what to do>"}],
  "savings_opportunities": [{"opportunity": "<description>", "estimated_saving": "<amount or %>", "effort": "low|medium|high", "timeframe": "<when>"}],
  "alerts": [{"level": "critical|warning|positive", "message": "<concise alert>"}],
  "recommendations": [{"title": "<decision>", "why": "<why this matters>", "evidence": ["<supporting data>"], "recommended_action": "<specific next step>", "expected_impact": "<amount or not quantified>", "urgency": "high|medium|low", "confidence": "high|medium|low", "effort": "low|medium|high", "priority_score": 0}],
  "total_recoverable": "<total estimated savings per year>",
  "priority_actions": ["<action 1>", "<action 2>", "<action 3>"],
  "data_quality": "good|partial|insufficient",
  "missing_data": ["<field missing if any>"],
  "evidence": ["<factual support for the most important insights>"]
}"""
    },
    "financial": {
        "role": "CFO-level financial analyst producing a P&L-style management overview",
        "tone": "authoritative, precise, structured for board-level consumption. No fluff.",
        "context": "Companies submit financial data in any form — P&L statements, accounting software exports (Primavera, SAP, Sage, PHC, QuickBooks), balance sheets, bank statements, or free-form summaries.",
        "schema": """{
  "report_type": "financial",
  "period": "<period label>",
  "executive_summary": "<3-5 sentence financial health summary>",
  "income_statement": {
    "revenue": "<total revenue>",
    "cogs": "<cost of goods/services sold>",
    "gross_profit": "<revenue minus COGS>",
    "gross_margin_pct": "<gross margin %>",
    "operating_expenses": "<total opex>",
    "ebitda": "<EBITDA>",
    "net_result": "<net profit or loss>"
  },
  "kpis": [{"label": "<KPI>", "value": "<value>", "change": "<vs previous>", "trend": "up|down|neutral"}],
  "cash_flow_summary": "<narrative on cash position>",
  "cost_structure": [{"category": "<category>", "amount": "<value>", "pct_of_revenue": "<%>"}],
  "alerts": [{"level": "critical|warning|positive", "message": "<alert>"}],
  "recommendations": [{"title": "<decision>", "why": "<why this matters>", "evidence": ["<supporting data>"], "recommended_action": "<specific next step>", "expected_impact": "<amount or not quantified>", "urgency": "high|medium|low", "confidence": "high|medium|low", "effort": "low|medium|high", "priority_score": 0}],
  "forecast": {
    "next_period": "<label>",
    "revenue_estimate": "<value>",
    "ebitda_estimate": "<value>",
    "confidence": "high|medium|low",
    "rationale": "<2-3 sentences>",
    "actions": ["<action 1>", "<action 2>"]
  },
  "financial_narrative": "<3-4 sentences on financial position and trajectory>",
  "data_quality": "good|partial|insufficient",
  "missing_data": ["<field missing if any>"],
  "evidence": ["<factual support for the most important insights>"]
}"""
    }
}

# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return JSONResponse(health_payload(os.environ), headers={"Cache-Control": "no-store"})

# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/auth/register")
@limiter.limit("10/hour")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    company_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    email = sanitize(email, 200).lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="A password deve ter pelo menos 8 caracteres.")

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Este email já tem conta. Faça login.")

    user = User(email=email, password_hash=hash_password(password), email_verified=False)
    workspace = Workspace(company_name=sanitize(company_name, 200) or email, plan="free")
    db.add_all([user, workspace])
    await db.flush()

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await db.commit()

    token = issue_session_token(user.id, workspace.id, "owner")
    return JSONResponse({"token": token, "plan": "free"})


@app.post("/auth/login")
@limiter.limit("20/hour")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = sanitize(email, 200).lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou password incorrectos.")

    result = await db.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
    membership = result.scalars().first()
    if not membership:
        raise HTTPException(status_code=400, detail="Conta sem workspace associado. Contacte o suporte.")

    token = issue_session_token(user.id, membership.workspace_id, membership.role)
    return JSONResponse({"token": token})


@app.post("/auth/google")
@limiter.limit("20/hour")
async def auth_google(
    request: Request,
    id_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    info = verify_google_token(id_token)
    email = info["email"].lower()

    result = await db.execute(select(User).where(User.google_id == info["google_id"]))
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = info["google_id"]
            if not user.email_verified:
                user.email_verified = True
        else:
            user = User(email=email, google_id=info["google_id"], email_verified=True)
            db.add(user)
            await db.flush()
            workspace = Workspace(company_name=email, plan="free")
            db.add(workspace)
            await db.flush()
            db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))

    await db.commit()

    result = await db.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
    membership = result.scalars().first()
    if not membership:
        raise HTTPException(status_code=400, detail="Conta sem workspace associado.")

    token = issue_session_token(user.id, membership.workspace_id, membership.role)
    return JSONResponse({"token": token})


@app.get("/auth/session")
async def auth_session(
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    session_token = None
    try:
        session = await get_current_session(db, x_token)
    except HTTPException:
        magic_payload = await consume_magic_link_token(db, x_token or "")
        workspace = await db.get(Workspace, magic_payload["workspace_id"])
        user = await db.get(User, magic_payload["user_id"])
        if not workspace or not user:
            await db.rollback()
            raise HTTPException(status_code=401, detail="Sessão inválida.")
        membership_result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
        )
        membership = membership_result.scalar_one_or_none()
        if not membership:
            await db.rollback()
            raise HTTPException(status_code=401, detail="Sessão inválida.")
        session_token = issue_session_token(user.id, workspace.id, membership.role)
        await db.commit()
        session = {"user": user, "workspace": workspace, "role": membership.role}

    user, workspace, role = session["user"], session["workspace"], session["role"]
    limits = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])
    remaining = await get_remaining_quota(db, workspace)

    return JSONResponse({
        "plan": workspace.plan,
        "role": role,
        "chatbot": limits["chatbot"],
        "remaining": remaining,
        "email": user.email,
        "company_name": workspace.company_name,
        "needs_password": user.password_hash is None,  # true para contas criadas via Stripe/Google sem password própria ainda
        **({"token": session_token} if session_token else {}),
    })


@app.post("/auth/set-password")
@limiter.limit("10/hour")
async def set_password(
    request: Request,
    password: str = Form(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Usado quando alguém entra via link de pagamento (magic link) ou Google
    e ainda não tem password própria definida."""
    session = await get_current_session(db, x_token)
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="A password deve ter pelo menos 8 caracteres.")
    session["user"].password_hash = hash_password(password)
    await db.commit()
    return JSONResponse({"ok": True})


@app.post("/workspace/invite")
@limiter.limit("10/hour")
async def invite_manager(
    request: Request,
    email: str = Form(...),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    session = await get_current_session(db, x_token)
    if session["role"] != "owner":
        raise HTTPException(status_code=403, detail="Só o dono pode convidar gestores.")

    workspace = session["workspace"]
    limit = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])["max_members"]
    current = await count_workspace_members(db, workspace.id)
    if current >= limit:
        raise HTTPException(status_code=400, detail=f"Limite de {limit} perfis atingido para o plano {workspace.plan}.")

    email = sanitize(email, 200).lower()
    result = await db.execute(select(User).where(User.email == email))
    invited_user = result.scalar_one_or_none()
    if not invited_user:
        invited_user = User(email=email, email_verified=False)
        db.add(invited_user)
        await db.flush()

    existing_membership = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == invited_user.id,
        )
    )
    if existing_membership.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Esta pessoa já pertence ao workspace.")

    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=invited_user.id, role="manager"))
    invite_token = issue_magic_link_token(db, invited_user.id, workspace.id, "manager")
    await db.commit()
    if RESEND_API_KEY:
        try:
            send_invite_email(email, workspace.company_name, invite_token)
        except Exception as e:
            print(f"Erro ao enviar convite: {e}")

    return JSONResponse({"ok": True, "email": email})

# ═══════════════════════════════════════════════════════════════════════════
# ANÁLISE
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/analyze-sales")
@limiter.limit("20/hour")
async def analyze_sales(
    request: Request,
    input_mode:      str  = Form("text"),
    report_type:     str  = Form("sales"),
    raw_data:        str  = Form(""),
    period_current:  str  = Form(""),
    period_previous: str  = Form(""),
    revenue_current: str  = Form(""),
    revenue_previous:str  = Form(""),
    units_current:   str  = Form(""),
    units_previous:  str  = Form(""),
    top_products:    str  = Form(""),
    top_clients:     str  = Form(""),
    top_salespeople: str  = Form(""),
    op_costs:        str  = Form(""),
    extra_notes:     str  = Form(""),
    team_data:       str  = Form(""),
    output_metric:   str  = Form(""),
    total_labor_cost:str  = Form(""),
    total_costs:     str  = Form(""),
    cogs:            str  = Form(""),
    opex:            str  = Form(""),
    net_result:      str  = Form(""),
    company_name:    str  = Form(""),
    currency:        str  = Form("EUR"),
    file1: Optional[UploadFile] = File(default=None),
    file2: Optional[UploadFile] = File(default=None),
    file3: Optional[UploadFile] = File(default=None),
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    session   = await get_current_session(db, x_token)
    workspace = session["workspace"]
    workspace_id = workspace.id
    user_id = session["user"].id
    await ensure_quota_available(db, workspace)
    await db.rollback()  # fecha a transação read-only antes da chamada Anthropic

    company_name = sanitize(company_name, 100)
    currency     = sanitize(currency, 10) or "EUR"
    report_type  = report_type if report_type in REPORT_PROMPTS else "sales"
    cfg          = REPORT_PROMPTS[report_type]
    input_fields = {
        "period_current": period_current,
        "period_previous": period_previous,
        "revenue_current": revenue_current,
        "revenue_previous": revenue_previous,
        "units_current": units_current,
        "units_previous": units_previous,
        "top_products": top_products,
        "top_clients": top_clients,
        "top_salespeople": top_salespeople,
        "op_costs": op_costs,
        "extra_notes": extra_notes,
        "team_data": team_data,
        "output_metric": output_metric,
        "total_labor_cost": total_labor_cost,
        "total_costs": total_costs,
        "cogs": cogs,
        "opex": opex,
        "net_result": net_result,
    }
    deterministic = build_deterministic_analysis(report_type, currency, input_fields)

    message_content = []
    text_blocks     = []

    if input_mode == "file":
        files = [f for f in [file1, file2, file3] if f and f.filename]
        if not files:
            raise HTTPException(status_code=400, detail="Por favor seleccione pelo menos um ficheiro.")
        for i, f in enumerate(files[:3]):
            text, pdf_bytes = await process_file(f)
            label = f"Ficheiro {i+1}: {f.filename}"
            if pdf_bytes:
                encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")
                message_content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}})
                message_content.append({"type": "text", "text": label + " (PDF acima)"})
            else:
                text_blocks.append(f"=== {label} ===\n{text}")
        if text_blocks:
            message_content.append({"type": "text", "text": "Dados em texto:\n\n" + "\n\n".join(text_blocks)})
        if len(files) > 1:
            message_content.append({"type": "text", "text": f"{len(files)} ficheiros fornecidos. Compara e analisa todos."})

    elif input_mode == "text":
        data = sanitize(raw_data, 15000)
        if not data:
            raise HTTPException(status_code=400, detail="Por favor cole os dados.")
        message_content = f"Dados fornecidos:\n\n{data}"

    else:  # form
        parts = build_manual_input_parts(
            report_type=report_type,
            currency=currency,
            period_current=period_current,
            period_previous=period_previous,
            revenue_current=revenue_current,
            revenue_previous=revenue_previous,
            units_current=units_current,
            units_previous=units_previous,
            top_products=top_products,
            top_clients=top_clients,
            top_salespeople=top_salespeople,
            op_costs=op_costs,
            extra_notes=extra_notes,
            team_data=team_data,
            output_metric=output_metric,
            total_labor_cost=total_labor_cost,
            total_costs=total_costs,
            cogs=cogs,
            opex=opex,
            net_result=net_result,
        )
        if not parts:
            raise HTTPException(status_code=400, detail="Por favor preencha pelo menos alguns campos.")
        message_content = "Dados do formulário:\n\n" + "\n".join(parts)

    system_prompt = f"""You are an {cfg['role']}.
Tone: {cfg['tone']}
Currency: {currency}. Company: {company_name or "the company"}.

{FORMATS_NOTICE}

Context: {cfg['context']}

{deterministic_prompt_block(deterministic)}

LANGUAGE: All text values in the JSON MUST be written in European Portuguese (Portugal).
This includes summaries, KPI labels, alert messages, narratives, recommendations, and all text fields.
Only keep English for JSON keys and enum values (trend, up, critical, etc.).

If multiple files or periods are provided, compare them directly.
If forecast_allowed is false in deterministic calculations, do not create a forecast; explain missing data instead.
Every recommendation must be specific, supported by evidence, include confidence, effort, urgency, expected_impact and priority_score.
Separate facts present in the data from hypotheses about causes. Do not invent external benchmarks.

Respond ONLY with valid JSON — no markdown, no preamble.
Schema:
{cfg['schema']}
"""

    if isinstance(message_content, list):
        message_content.append({"type": "text", "text": "Analisa todos os dados e gera o relatório JSON."})
    else:
        message_content += "\n\nGera o relatório no formato JSON especificado."

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=6000,
            system=system_prompt,
            messages=[{"role": "user", "content": message_content}],
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Serviço de análise indisponível.")

    raw = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    if getattr(response, "stop_reason", None) == "max_tokens":
        raise HTTPException(
            status_code=500,
            detail="A análise excedeu o limite de resposta do modelo. Tente novamente com menos dados.",
        )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="O modelo devolveu uma resposta inválida. Tente novamente.",
        )
    result = validate_and_enhance_report_result(result, report_type, deterministic, currency)

    # Grava no histórico — é isto que alimenta o dashboard de evolução do Team
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=401, detail="Sessão inválida.")

    analysis_record = Analysis(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        module_type=report_type,
        period_label=result.get("period"),
        result_json=result,
        data_quality=result.get("data_quality"),
    )
    try:
        db.add(analysis_record)
        await db.flush()
        remaining = await consume_quota_after_success(db, workspace)
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    result["analyses_remaining"] = remaining
    return JSONResponse(content=result)


@app.get("/history/{module_type}")
async def get_module_history(
    module_type: str,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Histórico de relatórios de um módulo — só Team (dashboard de evolução)."""
    session = await get_current_session(db, x_token)
    workspace = session["workspace"]
    if workspace.plan != "team":
        raise HTTPException(status_code=403, detail="Histórico disponível apenas no plano Team.")

    result = await db.execute(
        select(Analysis)
        .where(Analysis.workspace_id == workspace.id, Analysis.module_type == module_type)
        .order_by(Analysis.created_at.asc())
    )
    records = result.scalars().all()
    return JSONResponse({
        "history": [
            {
                "id": str(r.id),
                "period": r.period_label,
                "data_quality": r.data_quality,
                "created_at": r.created_at.isoformat(),
                "result": r.result_json,
            }
            for r in records
        ]
    })

# ── CHAT (Team / Admin / Test only) ──────────────────────────────────────────

@app.post("/chat")
@limiter.limit("60/hour")
async def chat(
    request: Request,
    x_token: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    session = await get_current_session(db, x_token)
    workspace = session["workspace"]
    limits = PLAN_LIMITS.get(workspace.plan, PLAN_LIMITS["free"])
    if not limits["chatbot"]:
        raise HTTPException(
            status_code=403,
            detail="O chatbot está disponível a partir do plano Pro. Faça upgrade para aceder."
        )

    body           = await request.json()
    messages       = body.get("messages", [])
    all_reports    = body.get("all_reports", {})
    report_context = body.get("report_context", None)
    company_name   = body.get("company_name", "a empresa")
    currency       = body.get("currency", "EUR")
    asking_for_data = body.get("asking_for_data", False)

    if not messages:
        raise HTTPException(status_code=400, detail="Sem mensagens.")

    messages = messages[-24:]

    reports_block = ""
    if all_reports:
        generated = {k: v for k, v in all_reports.items() if v}
        if generated:
            parts = []
            for rtype, rdata in generated.items():
                parts.append(f"\n--- RELATÓRIO {rtype.upper()} ---\n" + json.dumps(rdata, indent=2, ensure_ascii=False))
            reports_block = "\n\nRELATÓRIOS DA SESSÃO:" + "".join(parts)
    elif report_context:
        reports_block = "\n\nRELATÓRIO:\n" + json.dumps(report_context, indent=2, ensure_ascii=False)

    if asking_for_data:
        system_prompt = """És um assistente de recolha de dados. O relatório teve dados insuficientes.
Faz UMA pergunta de cada vez para recolher a informação em falta. Sê específico sobre o formato.
Quando tiveres informação suficiente, começa a resposta exactamente com: DADOS_SUFICIENTES"""
    else:
        system_prompt = f"""És um consultor financeiro e operacional integrado no SalesLab.
Falas directamente com o gestor ou dono da empresa.

FORMATAÇÃO — OBRIGATÓRIA:
- Usa SEMPRE markdown
- Começa SEMPRE com ## que resume o tema
- Usa bullet points para listas de KPIs, factos e recomendações
- Destaca números com **bold** — ex: **€12.400**, **+23%**, **CRÍTICO**
- Termina SEMPRE com ## Próximo Passo com 1 acção concreta

CONTEÚDO:
- Usa os números reais dos relatórios — nunca inventes valores
- Cruza informação entre módulos quando disponível
- Dá conselhos com valores concretos: "se cortares X poupas Y em Z meses"
- Sê directo como um CFO que cobra €500/hora

Empresa: {company_name} | Moeda: {currency}{reports_block}

Se não houver relatórios, pede para gerar um primeiro.
Responde sempre no idioma em que o utilizador escreve."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_prompt,
            messages=messages,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Serviço de chat indisponível.")

    reply = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    has_enough_data = reply.startswith("DADOS_SUFICIENTES")
    if has_enough_data:
        reply = reply.replace("DADOS_SUFICIENTES", "").strip()

    return JSONResponse({"message": reply, "has_enough_data": has_enough_data})

# ═══════════════════════════════════════════════════════════════════════════
# STRIPE + RESEND
# ═══════════════════════════════════════════════════════════════════════════

import stripe as stripe_lib
import resend as resend_lib

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
FRONTEND_URL          = os.environ.get("FRONTEND_URL", "https://app.saleslab.cc")

stripe_lib.api_key = STRIPE_SECRET_KEY
resend_lib.api_key = RESEND_API_KEY

# ATENÇÃO: substitui pelos 2 price_id novos (Pro €19,99 / Team €59,99) no Stripe Dashboard
PRICE_TO_PLAN = {
    "price_1Tw2gzF6K4YHMTtacoJh9S8f":  "pro",
    "price_1Tw2gzF6K4YHMTtahI75WYBJ":  "pro",
    "price_1Tw2k5F6K4YHMTtaEkdDnpnX":  "team",
    "price_1Tw2k5F6K4YHMTta2UXJNeg5":  "team",
}

PLAN_LABELS = {"pro": "Pro", "team": "Team"}


def send_magic_link_email(email: str, plan: str, login_token: str):
    """Substitui o antigo send_access_email — já não envia um código para
    colar, envia um link que faz login directo (a app trata o resto)."""
    label = PLAN_LABELS.get(plan, plan.capitalize())
    login_url = f"{FRONTEND_URL}/?login_token={login_token}"
    html = f"""
    <div style="background:#080808;padding:48px 32px;font-family:Georgia,serif;max-width:520px;margin:0 auto">
      <div style="margin-bottom:32px">
        <span style="color:#f0ede8;font-size:24px;font-weight:700">Sales</span>
        <span style="color:#c9a84c;font-size:24px;font-weight:700">Lab</span>
      </div>
      <h1 style="color:#f0ede8;font-size:28px;font-weight:300;margin-bottom:8px">
        Bem-vindo ao plano {label}.
      </h1>
      <p style="color:#888;font-size:15px;line-height:1.7;margin-bottom:32px">
        O teu acesso está pronto. Clica no botão abaixo para entrares directamente.
      </p>
      <a href="{login_url}" style="display:inline-block;background:#c9a84c;color:#000;padding:14px 28px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:700;margin-bottom:32px">
        Entrar na plataforma →
      </a>
      <p style="color:#444;font-size:12px;line-height:1.6">
        Depois de entrares, vais poder definir a tua password para futuros logins.<br>
        Dúvidas? Responde a este email ou contacta geral@saleslab.cc
      </p>
    </div>
    """
    resend_lib.Emails.send({
        "from":    "SalesLab <noreply@saleslab.cc>",
        "to":      [email],
        "subject": f"O teu acesso SalesLab {label} está pronto",
        "html":    html,
    })


def send_invite_email(email: str, company_name: str, invite_token: str):
    login_url = f"{FRONTEND_URL}/?login_token={invite_token}"
    html = f"""
    <div style="background:#080808;padding:48px 32px;font-family:Georgia,serif;max-width:520px;margin:0 auto">
      <h1 style="color:#f0ede8;font-size:24px;font-weight:300">Foste convidado para {company_name} no SalesLab</h1>
      <p style="color:#888;font-size:15px;line-height:1.7;margin:16px 0 32px">
        Tens acesso como gestor. Clica para entrar e definires a tua password.
      </p>
      <a href="{login_url}" style="display:inline-block;background:#c9a84c;color:#000;padding:14px 28px;border-radius:6px;text-decoration:none;font-weight:700">
        Entrar →
      </a>
    </div>
    """
    resend_lib.Emails.send({
        "from": "SalesLab <noreply@saleslab.cc>",
        "to": [email],
        "subject": f"Convite para {company_name} no SalesLab",
        "html": html,
    })


@app.post("/create-checkout-session")
@limiter.limit("10/hour")
async def create_checkout_session(request: Request):
    body = await request.json()
    price_id = body.get("price_id", "")
    email    = body.get("email", "").lower().strip()

    print(f"DEBUG price_id recebido: {repr(price_id)}")
    print(f"DEBUG chaves conhecidas: {list(PRICE_TO_PLAN.keys())}")
    if price_id not in PRICE_TO_PLAN:
        raise HTTPException(status_code=400, detail="Plano inválido.")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe não configurado.")

    try:
        session = stripe_lib.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email if email else None,
            success_url=f"{FRONTEND_URL}?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?payment=cancelled",
            metadata={"price_id": price_id},
        )
        return JSONResponse({"url": session.url})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar sessão: {str(e)}")


SUBSCRIPTION_ACCESS_STATUSES = {"active", "trialing", "past_due"}
SUBSCRIPTION_TERMINAL_STATUSES = {"canceled", "incomplete_expired", "unpaid", "paused"}


def stripe_get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            pass
    return getattr(obj, key, default)


def stripe_timestamp_to_datetime(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc)


def get_subscription_price_id(subscription_obj) -> str:
    items = stripe_get(subscription_obj, "items", {})
    data = stripe_get(items, "data", []) or []
    if not data:
        return ""
    price = stripe_get(data[0], "price", {})
    return stripe_get(price, "id", "") or ""


def subscription_plan_from_stripe(subscription_obj) -> str | None:
    return PRICE_TO_PLAN.get(get_subscription_price_id(subscription_obj))


def apply_subscription_state_to_workspace(workspace: Workspace, subscription_obj, plan: str | None = None):
    """Apply Stripe subscription state to a workspace using Stripe as source of truth."""
    status = stripe_get(subscription_obj, "status", "") or ""
    effective_plan = plan or subscription_plan_from_stripe(subscription_obj)

    workspace.stripe_subscription_id = stripe_get(subscription_obj, "id", workspace.stripe_subscription_id)
    workspace.stripe_customer_id = stripe_get(subscription_obj, "customer", workspace.stripe_customer_id)
    workspace.stripe_subscription_status = status or None
    workspace.stripe_current_period_end = stripe_timestamp_to_datetime(
        stripe_get(subscription_obj, "current_period_end")
    )
    workspace.stripe_cancel_at_period_end = bool(stripe_get(subscription_obj, "cancel_at_period_end", False))
    workspace.stripe_cancel_at = stripe_timestamp_to_datetime(stripe_get(subscription_obj, "cancel_at"))

    if status in SUBSCRIPTION_ACCESS_STATUSES and effective_plan:
        workspace.plan = effective_plan
        workspace.plan_renewed_at = datetime.now(timezone.utc)
    elif status in SUBSCRIPTION_TERMINAL_STATUSES:
        workspace.plan = "free"


def apply_failed_invoice_state_to_workspace(workspace: Workspace, subscription_obj):
    """Sync failed-payment state without prematurely removing access during recovery."""
    apply_subscription_state_to_workspace(workspace, subscription_obj)


async def claim_stripe_event(db: AsyncSession, event_id: str, event_type: str) -> bool:
    stmt = (
        pg_insert(StripeWebhookEvent)
        .values(id=event_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=[StripeWebhookEvent.id])
        .returning(StripeWebhookEvent.id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def find_workspace_for_stripe_subscription(
    db: AsyncSession,
    subscription_id: str | None = None,
    customer_id: str | None = None,
    email: str | None = None,
):
    if subscription_id:
        result = await db.execute(select(Workspace).where(Workspace.stripe_subscription_id == subscription_id))
        workspace = result.scalar_one_or_none()
        if workspace:
            return workspace

    if customer_id:
        result = await db.execute(select(Workspace).where(Workspace.stripe_customer_id == customer_id))
        workspace = result.scalar_one_or_none()
        if workspace:
            return workspace

    if email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            result = await db.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
            membership = result.scalars().first()
            if membership:
                return await db.get(Workspace, membership.workspace_id)

    return None


async def get_or_create_checkout_workspace(db: AsyncSession, email: str, plan: str):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        result = await db.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
        membership = result.scalars().first()
        workspace = await db.get(Workspace, membership.workspace_id) if membership else None
    else:
        user = User(email=email, email_verified=True)  # confirmado pelo Stripe
        db.add(user)
        await db.flush()
        workspace = None

    if not workspace:
        workspace = Workspace(company_name=email, plan=plan)
        db.add(workspace)
        await db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))

    return user, workspace


async def retrieve_stripe_subscription(subscription_id: str):
    if not subscription_id:
        return None
    return stripe_lib.Subscription.retrieve(subscription_id)


async def process_checkout_completed(db: AsyncSession, session_obj):
    email = (
        stripe_get(session_obj, "customer_email")
        or stripe_get(stripe_get(session_obj, "customer_details", {}), "email", "")
    ).lower()
    price_id = stripe_get(stripe_get(session_obj, "metadata", {}), "price_id", "")
    plan = PRICE_TO_PLAN.get(price_id)
    customer_id = stripe_get(session_obj, "customer")
    subscription_id = stripe_get(session_obj, "subscription")

    if not email or not plan:
        return None, None, None

    user, workspace = await get_or_create_checkout_workspace(db, email, plan)
    subscription_obj = await retrieve_stripe_subscription(subscription_id)

    if subscription_obj:
        apply_subscription_state_to_workspace(workspace, subscription_obj, plan=plan)
    else:
        workspace.plan = plan
        workspace.stripe_customer_id = customer_id
        workspace.stripe_subscription_id = subscription_id
        workspace.stripe_subscription_status = "active"
        workspace.plan_renewed_at = datetime.now(timezone.utc)

    return user, workspace, plan


async def process_subscription_event(
    db: AsyncSession,
    subscription_obj,
    *,
    payment_failed: bool = False,
    allow_deleted_snapshot_fallback: bool = False,
):
    snapshot_subscription_id = stripe_get(subscription_obj, "id")
    current_subscription = await retrieve_stripe_subscription(snapshot_subscription_id)
    source_subscription = current_subscription

    if not source_subscription and allow_deleted_snapshot_fallback:
        source_subscription = subscription_obj

    if not source_subscription:
        return None

    subscription_id = stripe_get(source_subscription, "id")
    customer_id = stripe_get(source_subscription, "customer")
    workspace = await find_workspace_for_stripe_subscription(db, subscription_id, customer_id)
    if not workspace:
        return None

    if payment_failed:
        apply_failed_invoice_state_to_workspace(workspace, source_subscription)
    else:
        apply_subscription_state_to_workspace(workspace, source_subscription)
    return workspace


async def process_invoice_event(db: AsyncSession, invoice_obj, *, payment_failed: bool = False):
    subscription_id = stripe_get(invoice_obj, "subscription")
    subscription_obj = await retrieve_stripe_subscription(subscription_id)
    if not subscription_obj:
        return None
    return await process_subscription_event(db, subscription_obj, payment_failed=payment_failed)


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret não configurado.")

    try:
        event = stripe_lib.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe_lib.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Assinatura inválida.")

    event_id = stripe_get(event, "id", "")
    event_type = stripe_get(event, "type", "")
    if event_id:
        claimed = await claim_stripe_event(db, event_id, event_type)
        if not claimed:
            await db.rollback()
            return JSONResponse({"status": "ok", "duplicate": True})

    try:
        event_obj = stripe_get(stripe_get(event, "data", {}), "object", {})
        user = None
        workspace = None
        plan = None

        if event_type == "checkout.session.completed":
            user, workspace, plan = await process_checkout_completed(db, event_obj)
        elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
            await process_subscription_event(db, event_obj)
        elif event_type == "customer.subscription.deleted":
            await process_subscription_event(db, event_obj, allow_deleted_snapshot_fallback=True)
        elif event_type == "invoice.paid":
            await process_invoice_event(db, event_obj)
        elif event_type == "invoice.payment_failed":
            await process_invoice_event(db, event_obj, payment_failed=True)

        login_token = None
        if event_type == "checkout.session.completed" and user and workspace and plan and RESEND_API_KEY:
            login_token = issue_magic_link_token(db, user.id, workspace.id, "owner")

        await db.commit()

        if login_token:
            try:
                send_magic_link_email(user.email, plan, login_token)
            except Exception as e:
                print(f"Erro ao enviar email: {e}")

    except Exception:
        await db.rollback()
        raise

    return JSONResponse({"status": "ok"})

# ═══════════════════════════════════════════════════════════════════════════
# ADMIN (uso interno, para testes)
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/admin/create-workspace")
async def admin_create_workspace(
    request: Request,
    email: str = Form(...),
    plan: str = Form(...),
    company_name: str = Form(""),
    x_admin_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not x_admin_secret or not hmac.compare_digest(x_admin_secret.strip(), ADMIN_SECRET):
        raise HTTPException(status_code=403, detail="Acesso negado.")
    if plan not in PLAN_LIMITS:
        raise HTTPException(status_code=400, detail=f"Plano inválido: {plan}")

    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, email_verified=True)
        db.add(user)
        await db.flush()

    workspace = Workspace(company_name=company_name or email, plan=plan)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await db.commit()

    token = issue_session_token(user.id, workspace.id, "owner")
    return JSONResponse({"token": token, "plan": plan, "email": email})
