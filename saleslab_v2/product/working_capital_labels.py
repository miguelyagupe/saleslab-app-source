"""Localized operating working capital display labels."""

NWC = "reported_operating_nwc"
CHANGE = "reported_operating_nwc_change"
WORKING_CAPITAL_METRIC_LABELS = {
    NWC: (
        "Fundo de maneio operacional reportado",
        "Reported operating net working capital",
        "Capital circulante operativo publicado",
        "Fonds de roulement opérationnel publié",
    ),
    "opening_reported_operating_nwc": (
        "Fundo de maneio operacional inicial reportado",
        "Reported opening operating NWC",
        "Capital circulante operativo inicial publicado",
        "Fonds de roulement opérationnel initial publié",
    ),
    CHANGE: (
        "Variação reportada do fundo de maneio · não é efeito em caixa",
        "Reported operating NWC balance change · not a cash flow",
        "Variación publicada del circulante · no es flujo de caja",
        "Variation publiée du fonds de roulement · pas un flux de trésorerie",
    ),
    "operating_nwc_change": (
        "Variação calculada do fundo de maneio · não é efeito em caixa",
        "Calculated operating NWC balance change · not a cash flow",
        "Variación calculada del circulante · no es flujo de caja",
        "Variation calculée du fonds de roulement · pas un flux de trésorerie",
    ),
}
