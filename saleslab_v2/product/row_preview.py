"""Presentation-only guidance for actual normalization issues in uploaded rows."""

from dataclasses import asdict

from saleslab_v2.analyst.context import unpack
from saleslab_v2.domain.idempotency import canonicalize

LANGUAGES = ("pt", "en", "es", "fr")
GUIDANCE = {
    "mapping": (
        "Confirme a rubrica na lista apenas se corresponder ao significado da linha original.",
        "Confirm a listed concept only if it matches the meaning of the original row.",
        "Confirme una partida de la lista solo si coincide con el significado de la línea original.",
        "Confirmez une rubrique de la liste uniquement si elle correspond au sens de la ligne originale.",
    ),
    "number": (
        "Reveja o valor original e o formato dos números escolhido para este ficheiro.",
        "Review the original amount and the number locale selected for this file.",
        "Revise el importe original y el formato numérico seleccionado para este archivo.",
        "Vérifiez le montant original et le format numérique choisi pour ce fichier.",
    ),
    "number.parentheses_sign_ambiguous": (
        "Confirme se os parênteses indicam valores negativos em todo o ficheiro e volte a validar.",
        "Confirm whether parentheses indicate negative values throughout the file, then validate again.",
        "Confirme si los paréntesis indican valores negativos en todo el archivo y vuelva a validar.",
        "Confirmez si les parenthèses indiquent des valeurs négatives dans tout le fichier, puis validez à nouveau.",
    ),
    "number.conflicting_sign": (
        "O valor combina parênteses e um sinal escrito. Confirme a convenção na fonte e corrija o valor no ficheiro.",
        "The amount combines parentheses and a written sign. Check the source convention and correct the file value.",
        "El importe combina paréntesis y un signo escrito. Confirme la convención en la fuente y corrija el archivo.",
        "Le montant combine des parenthèses et un signe écrit. Vérifiez la convention à la source et corrigez le fichier.",
    ),
    "sign.convention_confirmation_required": (
        "Esta saída de caixa tem valor positivo. Confirme o sinal na fonte; a aplicação não o inverte automaticamente.",
        "This cash outflow is positive. Confirm the sign against the source; the application does not reverse it automatically.",
        "Esta salida de caja es positiva. Confirme el signo en la fuente; la aplicación no lo invierte automáticamente.",
        "Cette sortie de trésorerie est positive. Vérifiez le signe à la source ; l’application ne l’inverse pas automatiquement.",
    ),
    "currency": (
        "Confirme a moeda na fonte e use um código ISO de três letras consistente com o valor original.",
        "Confirm the source currency and use a three-letter ISO code consistent with the original amount.",
        "Confirme la moneda en la fuente y use un código ISO de tres letras coherente con el importe original.",
        "Vérifiez la devise à la source et utilisez un code ISO de trois lettres cohérent avec le montant original.",
    ),
    "scale": (
        "Confirme a escala na fonte: raw (unidades), thousands (milhares) ou millions (milhões), sem conflito com o valor.",
        "Confirm the source scale: raw (units), thousands or millions, consistent with the amount.",
        "Confirme la escala en la fuente: raw (unidades), thousands (miles) o millions (millones), coherente con el importe.",
        "Vérifiez l’échelle à la source : raw (unités), thousands (milliers) ou millions (millions), cohérente avec le montant.",
    ),
    "period.fiscal_calendar_required": (
        "Confirme o mês de fecho fiscal deste ficheiro e volte a validar. FY 2026 identifica o exercício que termina em 2026.",
        "Confirm this file’s fiscal year-end month and validate again. FY 2026 identifies the fiscal year ending in 2026.",
        "Confirme el mes de cierre fiscal del archivo y vuelva a validar. FY 2026 identifica el ejercicio que termina en 2026.",
        "Confirmez le mois de clôture fiscale du fichier et validez à nouveau. FY 2026 désigne l’exercice se terminant en 2026.",
    ),
    "period.quarter_basis_ambiguous": (
        "Há um calendário fiscal confirmado, mas o trimestre não identifica a base. Confirme na fonte se é fiscal (por exemplo, FY 2026 Q1).",
        "A fiscal calendar is confirmed, but the quarter’s basis is unspecified. Check the source to confirm whether it is fiscal (for example, FY 2026 Q1).",
        "Hay un calendario fiscal confirmado, pero falta la base del trimestre. Confirme en la fuente si es fiscal (por ejemplo, FY 2026 Q1).",
        "Un calendrier fiscal est confirmé, mais la base du trimestre est indéterminée. Vérifiez à la source s’il est fiscal (par exemple, FY 2026 Q1).",
    ),
    "period": (
        "Confirme o período original. Use um ano, mês ou trimestre inequívoco; não presuma calendário fiscal nem corte YTD.",
        "Confirm the original period. Use an unambiguous year, month or quarter; do not assume a fiscal calendar or YTD cutoff.",
        "Confirme el período original. Use un año, mes o trimestre inequívoco; no suponga el calendario fiscal ni el corte YTD.",
        "Vérifiez la période originale. Utilisez une année, un mois ou un trimestre sans ambiguïté ; ne présumez ni calendrier fiscal ni date d’arrêté YTD.",
    ),
    "observation.duplicate_candidate": (
        "Há várias linhas candidatas para a mesma rubrica e período. Reveja-as na fonte; nenhuma é selecionada nem somada automaticamente.",
        "Several rows are candidates for the same concept and period. Review them against the source; none is selected or summed automatically.",
        "Hay varias líneas candidatas para la misma partida y período. Revíselas en la fuente; ninguna se selecciona ni se suma automáticamente.",
        "Plusieurs lignes correspondent à la même rubrique et période. Vérifiez-les à la source ; aucune n’est sélectionnée ni additionnée automatiquement.",
    ),
    "ratio": (
        "Este rácio exige operandos verificáveis. O modelo de upload não recolhe numerador e denominador; não converta a percentagem num valor monetário.",
        "This ratio requires verifiable operands. The upload template does not collect numerator and denominator; do not turn the percentage into a monetary amount.",
        "Este ratio requiere operandos verificables. La plantilla no recoge numerador y denominador; no convierta el porcentaje en un importe monetario.",
        "Ce ratio exige des opérandes vérifiables. Le modèle ne recueille pas de numérateur ni de dénominateur ; ne transformez pas le pourcentage en montant monétaire.",
    ),
    "unit": (
        "Confirme se a rubrica representa um valor monetário ou uma percentagem; as unidades não são intercambiáveis.",
        "Confirm whether the concept represents money or a percentage; these units are not interchangeable.",
        "Confirme si la partida representa un importe monetario o un porcentaje; las unidades no son intercambiables.",
        "Vérifiez si la rubrique représente un montant monétaire ou un pourcentage ; ces unités ne sont pas interchangeables.",
    ),
    "working_capital": (
        "Confirme o mesmo perímetro operacional e as definições nos saldos e na variação publicada. Variação de saldo não equivale a efeito em caixa.",
        "Confirm the same operating perimeter and source definitions across balances and published change. A balance change is not a cash flow effect.",
        "Confirme el mismo perímetro operativo y las definiciones en saldos y variación publicada. Una variación de saldo no equivale a flujo de caja.",
        "Confirmez le même périmètre opérationnel et les définitions dans les soldes et la variation publiée. Une variation de solde n’est pas un flux de trésorerie.",
    ),
    "roic": (
        "Confirme NOPAT após impostos operacionais e capital investido médio positivo, na mesma moeda, período e perímetro, com pressupostos fiscais citados da fonte.",
        "Confirm after-operating-tax NOPAT and positive average invested capital in the same currency, period and scope, with source-quoted tax assumptions.",
        "Confirme NOPAT después de impuestos operativos y capital invertido medio positivo en la misma moneda, período y perímetro, con supuestos fiscales citados.",
        "Confirmez le NOPAT après impôts opérationnels et le capital investi moyen positif, dans la même devise, période et périmètre, avec hypothèses fiscales citées.",
    ),
    "fallback": (
        "Reveja este campo na fonte e volte a validar. Esta indicação não resolve a validação automaticamente.",
        "Review this field against the source and validate again. This guidance does not resolve validation automatically.",
        "Revise este campo en la fuente y vuelva a validar. Esta indicación no resuelve la validación automáticamente.",
        "Vérifiez ce champ à la source et validez à nouveau. Cette indication ne résout pas automatiquement la validation.",
    ),
}

# A currency attached to a percentage is a unit conflict, not a missing ISO code.
GUIDANCE["currency.percentage_conflict"] = GUIDANCE["unit"]


def row_preview(observation, original, language):
    index = LANGUAGES.index(language)
    guidance = []
    for issue in observation.issues:
        messages = GUIDANCE.get(
            issue.code, GUIDANCE.get(issue.code.split(".", 1)[0], GUIDANCE["fallback"])
        )
        guidance.append(
            {"code": issue.code, "field": issue.field, "message": messages[index]}
        )
    return {
        "location": observation.source_reference,
        "original_line": original["line"],
        **unpack(canonicalize(asdict(observation))),
        "original_input": dict(original),
        "validation_guidance": guidance,
    }
