"""
A que impuesto pertenece una retencion, visto desde ARCA y desde Xubio.

ARCA identifica el impuesto con un codigo (217 = Ganancias, 767 = IVA) y a
veces con su descripcion; en Xubio el impuesto solo se adivina por el nombre de
la cuenta ("Retenciones de IVA sufridas"). El cruce usa esta clave comun para
no emparejar una retencion de IVA con un asiento de Ganancias del mismo importe.
"""

import re
import unicodedata

from app.services.importador_tablas import _normalizar

# Codigos de impuesto de ARCA que aparecen en "Mis Retenciones".
CODIGOS_ARCA = {
    "217": "Ganancias",
    "767": "IVA",
}

# Palabras (normalizadas) que delatan el impuesto en un texto libre.
_PALABRAS = (
    ("IVA", ("IVA", "VALORAGREGADO")),
    ("Ganancias", ("GANANCIA",)),
    ("Ingresos Brutos", ("IIBB", "INGRESOSBRUTOS", "SIRCREB", "ARBA", "AGIP")),
    ("SUSS", ("SUSS", "SEGURIDADSOCIAL")),
)


def impuesto_de_texto(*textos) -> str | None:
    """'Retenciones de IVA sufridas' -> 'IVA'; None si no se reconoce."""
    for t in textos:
        if not t:
            continue
        codigo = str(t).strip()
        if codigo in CODIGOS_ARCA:
            return CODIGOS_ARCA[codigo]
        n = _normalizar(t)
        tokens = set(_palabras(t))
        for nombre, palabras in _PALABRAS:
            # Las siglas cortas van por palabra completa: "IVA" dentro de
            # "ACTIVA" no es IVA. Las largas pueden venir pegadas.
            if any((p in tokens) if len(p) <= 4 else (p in n) for p in palabras):
                return nombre
    return None


def _palabras(texto) -> list[str]:
    sin_acentos = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.findall(r"[A-Z0-9]+", sin_acentos.upper())
