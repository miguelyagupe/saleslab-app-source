"""Reviewed presentation labels only; never change canonical requirement codes."""

LANGUAGES = ("pt", "en", "es", "fr")
REQUIREMENTS = {
    "adequate_time_series": (
        "Histórico suficiente para previsões",
        "Sufficient history for forecasts",
        "Historial suficiente para previsiones",
        "Historique suffisant pour les prévisions",
    ),
    "audited_finding": (
        "Conclusão auditada que sustente uma ação",
        "Audited finding supporting an action",
        "Conclusión auditada que sustente una acción",
        "Conclusion auditée étayant une action",
    ),
    "backtest": (
        "Validação da previsão em dados históricos",
        "Forecast validation on historical data",
        "Validación de la previsión con datos históricos",
        "Validation des prévisions sur des données historiques",
    ),
    "baseline": (
        "Base de referência documentada",
        "Documented baseline",
        "Base de referencia documentada",
        "Base de référence documentée",
    ),
    "capex_schedule": (
        "Plano detalhado de investimento",
        "Detailed capital expenditure schedule",
        "Plan detallado de inversiones",
        "Calendrier détaillé des investissements",
    ),
    "complete_coverage": (
        "Cobertura completa dos dados necessários",
        "Complete coverage of required data",
        "Cobertura completa de los datos necesarios",
        "Couverture complète des données nécessaires",
    ),
    "cost": (
        "Custos necessários para avaliar o impacto",
        "Costs needed to assess impact",
        "Costes necesarios para evaluar el impacto",
        "Coûts nécessaires pour évaluer l’impact",
    ),
    "counterevidence_review": (
        "Revisão das evidências contraditórias",
        "Review of contradictory evidence",
        "Revisión de las evidencias contradictorias",
        "Examen des éléments contradictoires",
    ),
    "debt_schedule": (
        "Calendário detalhado da dívida",
        "Detailed debt schedule",
        "Calendario detallado de la deuda",
        "Échéancier détaillé de la dette",
    ),
    "driver": (
        "Fator explicativo documentado",
        "Documented explanatory driver",
        "Factor explicativo documentado",
        "Facteur explicatif documenté",
    ),
    "driver_bridge": (
        "Decomposição da variação por fatores",
        "Variance breakdown by driver",
        "Desglose de la variación por factores",
        "Décomposition de la variation par facteur",
    ),
    "enhanced_audit": (
        "Auditoria adicional exigida para esta capacidade",
        "Additional audit required for this capability",
        "Auditoría adicional exigida para esta capacidad",
        "Audit supplémentaire requis pour cette capacité",
    ),
    "lease_schedule": (
        "Calendário detalhado das locações",
        "Detailed lease schedule",
        "Calendario detallado de los arrendamientos",
        "Échéancier détaillé des contrats de location",
    ),
    "method": (
        "Método de análise documentado",
        "Documented analysis method",
        "Método de análisis documentado",
        "Méthode d’analyse documentée",
    ),
    "owner": (
        "Responsável identificado para a ação",
        "Identified action owner",
        "Responsable identificado para la acción",
        "Responsable de l’action identifié",
    ),
    "reconciled_driver": (
        "Fator explicativo reconciliado com os dados",
        "Explanatory driver reconciled to the data",
        "Factor explicativo conciliado con los datos",
        "Facteur explicatif rapproché des données",
    ),
    "relevant_components": (
        "Componentes necessários à análise",
        "Components required for the analysis",
        "Componentes necesarios para el análisis",
        "Composantes nécessaires à l’analyse",
    ),
    "risk": (
        "Avaliação dos riscos da ação",
        "Assessment of action risks",
        "Evaluación de los riesgos de la acción",
        "Évaluation des risques de l’action",
    ),
    "sensitivity": (
        "Análise de sensibilidade",
        "Sensitivity analysis",
        "Análisis de sensibilidad",
        "Analyse de sensibilité",
    ),
    "stable_scope": (
        "Perímetro de análise estável",
        "Stable analysis scope",
        "Perímetro de análisis estable",
        "Périmètre d’analyse stable",
    ),
    "validated_statement": (
        "Demonstração financeira validada",
        "Validated financial statement",
        "Estado financiero validado",
        "État financier validé",
    ),
}

HEADINGS = dict(
    zip(
        LANGUAGES,
        (
            "Dados necessários para análises adicionais",
            "Data needed for additional analyses",
            "Datos necesarios para análisis adicionales",
            "Données nécessaires pour des analyses supplémentaires",
        ),
    )
)
NOTICES = dict(
    zip(
        LANGUAGES,
        (
            "Estes requisitos indicam o que falta para outras capacidades. Não invalidam, por si só, as observações verificadas deste relatório.",
            "These requirements describe what is missing for other capabilities. They do not, on their own, invalidate this report’s verified observations.",
            "Estos requisitos indican lo que falta para otras capacidades. Por sí solos no invalidan las observaciones verificadas de este informe.",
            "Ces exigences indiquent ce qui manque pour d’autres capacités. Elles n’invalident pas, à elles seules, les observations vérifiées de ce rapport.",
        ),
    )
)


def requirement_text(code, language):
    labels = REQUIREMENTS.get(code)
    return f"{labels[LANGUAGES.index(language)]} ({code})" if labels else code
