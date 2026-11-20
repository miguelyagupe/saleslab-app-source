"""Close checklist labels; identifiers and source values remain evidence, not prose."""

LABELS = {
    "pt": (
        "Dados da demonstração",
        "Reconciliação",
        "Comparação",
        "Movimento esperado",
        "Confirmação da tarefa",
        "Qualidade dos dados",
    ),
    "en": (
        "Statement data",
        "Reconciliation",
        "Comparison",
        "Expected movement",
        "Task confirmation",
        "Data quality",
    ),
    "es": (
        "Datos del estado financiero",
        "Conciliación",
        "Comparación",
        "Movimiento esperado",
        "Confirmación de tarea",
        "Calidad de datos",
    ),
    "fr": (
        "Données de l’état financier",
        "Rapprochement",
        "Comparaison",
        "Mouvement attendu",
        "Confirmation de tâche",
        "Qualité des données",
    ),
}
CATEGORIES = (
    "statement",
    "reconciliation",
    "comparison",
    "movement",
    "task",
    "quality",
)


def label(category, language):
    return LABELS[language][CATEGORIES.index(category)]
