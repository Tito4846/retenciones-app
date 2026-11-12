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

# El codigo principal de cada impuesto: el que el bot consulta por defecto en
# "Mis Retenciones" (Ganancias e IVA, los que casi siempre tienen retenciones).
CODIGOS_PRINCIPALES = {
    "217": "Ganancias",
    "767": "IVA",
}

# Nombres de los impuestos que no son Ganancias ni IVA (asi se muestran y asi
# se cruzan: ARCA y Xubio tienen que dar el mismo).
PAIS = "Impuesto PAIS"
BIENES_PERSONALES = "Bienes Personales"
ITI = "Transferencia de Inmuebles"
PREMIOS = "Premios"
DONACIONES = "Donaciones"

# Todos los codigos que se reconocen por numero, aunque el archivo de ARCA venga
# sin la columna "Descripcion Impuesto": sin esto, una retencion 216 quedaba como
# "216" y no conciliaba contra la cuenta de IVA. Premios (466) y donaciones (222)
# van solo por su descripcion: no esta confirmado si ARCA los informa como
# Ganancias o aparte, y un codigo mal asignado impediria conciliarlos.
CODIGOS_ARCA = {
    **CODIGOS_PRINCIPALES,
    "210": "Ganancias",  # RG 830
    "787": "Ganancias",  # rentas de cuarta categoria (relacion de dependencia)
    "218": "Ganancias",  # beneficiarios del exterior
    "216": "IVA",  # SIRE
    "939": PAIS,
    "219": BIENES_PERSONALES,  # acciones y participaciones
    "172": ITI,
}

# Palabras (normalizadas) que delatan el impuesto en un texto libre.
_PALABRAS = (
    ("IVA", ("IVA", "VALORAGREGADO")),
    ("Ganancias", ("GANANCIA",)),
    ("Ingresos Brutos", ("IIBB", "INGRESOSBRUTOS", "SIRCREB", "ARBA", "AGIP")),
    ("SUSS", ("SUSS", "SEGURIDADSOCIAL")),
    # Van despues: "Retenciones Ganancias - premios" es Ganancias.
    (PAIS, ("PAIS",)),
    (BIENES_PERSONALES, ("BIENESPERSONALES", "BSPERSONALES", "BBPP")),
    (ITI, ("ITI", "TRANSFERENCIADEINMUEBLES", "TRANSFINMUEBLES")),
    (PREMIOS, ("PREMIO",)),
    (DONACIONES, ("DONACION",)),
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


def tipo_de_texto(*textos) -> str:
    """
    'RETENCION' / 'Percepción de Ganancias Sufrida' -> 'Retencion' / 'Percepcion';
    '' si no se reconoce. ARCA lo dice en "Descripción Operación"; en el mayor,
    el nombre de la cuenta.
    """
    for t in textos:
        n = _normalizar(t or "")
        reten, percep = "RETEN" in n, "PERCEP" in n
        if reten != percep:
            return "Retencion" if reten else "Percepcion"
    return ""


def _palabras(texto) -> list[str]:
    sin_acentos = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.findall(r"[A-Z0-9]+", sin_acentos.upper())
