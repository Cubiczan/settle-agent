"""Plain-language explanations for claim adjustment codes and common CPT codes.

CARC = Claim Adjustment Reason Code (X12 835). Group codes: CO = contractual
obligation (the patient never owes it), PR = patient responsibility, OA = other,
PI = payer-initiated. Only the codes the agent is allowed to explain are listed;
anything else is routed to billing staff rather than guessed at.
"""

GROUP = {
    "CO": {"en": "Insurance discount (you do not owe this)",
           "es": "Descuento del seguro (usted no debe esto)"},
    "PR": {"en": "Your share under your plan",
           "es": "Su parte según su plan"},
    "OA": {"en": "Other adjustment", "es": "Otro ajuste"},
    "PI": {"en": "Insurance adjustment", "es": "Ajuste del seguro"},
}

CARC = {
    "1": {"en": "applied to your deductible", "es": "aplicado a su deducible"},
    "2": {"en": "your coinsurance", "es": "su coseguro"},
    "3": {"en": "your copay", "es": "su copago"},
    "45": {"en": "amount above what your plan allows (the practice writes it off)",
           "es": "monto sobre lo que su plan permite (la clínica lo descuenta)"},
    "96": {"en": "a service your plan does not cover", "es": "un servicio que su plan no cubre"},
    "204": {"en": "not covered under your current benefit plan",
            "es": "no cubierto por su plan de beneficios actual"},
}

# Lay descriptions for the CPT codes that appear on statements. Not a coding
# reference - a patient-facing label.
CPT_LAY = {
    "99213": {"en": "Office visit (established patient, low complexity)",
              "es": "Consulta (paciente establecido, complejidad baja)"},
    "99214": {"en": "Office visit (established patient, moderate complexity)",
              "es": "Consulta (paciente establecido, complejidad moderada)"},
    "93000": {"en": "Electrocardiogram (ECG)", "es": "Electrocardiograma (ECG)"},
    "80053": {"en": "Comprehensive metabolic blood panel", "es": "Panel metabólico completo de sangre"},
    "36415": {"en": "Blood draw", "es": "Extracción de sangre"},
    "87880": {"en": "Rapid strep test", "es": "Prueba rápida de estreptococo"},
}


def explain_adjustment(group: str, carc: str, lang: str = "en") -> str | None:
    """Return a lay explanation, or None if the code is outside the allow-list."""
    if carc not in CARC or group not in GROUP:
        return None
    return CARC[carc][lang]


def cpt_label(cpt: str, lang: str = "en") -> str:
    entry = CPT_LAY.get(cpt)
    return entry[lang] if entry else ({"en": "Service", "es": "Servicio"}[lang] + f" {cpt}")
