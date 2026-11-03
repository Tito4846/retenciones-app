"""
Que cuenta del Libro Mayor de Xubio corresponde a cada consulta de ARCA.

En Xubio el mayor se mira cuenta por cuenta. No hay nombres fijos: la cuenta
se elige entre las que tiene el plan de cuentas de la empresa (las que ofrece
el campo «Cuenta» de Xubio), por el impuesto y el tipo de las retenciones de
ARCA. Retencion de Ganancias -> la cuenta que hable de retenciones de
Ganancias sufridas; con "Retencion y/o Percepcion", tambien la de percepciones.
"""

import unicodedata

from app.services.impuestos import impuesto_de_texto

RETENCION = "Retencion"
PERCEPCION = "Percepcion"
COMBINADO = "RetencionPercepcion"

_PALABRA_TIPO = {RETENCION: "RETEN", PERCEPCION: "PERCEP"}
ETIQUETA_TIPO = {RETENCION: "Retención", PERCEPCION: "Percepción"}
# Lo que la empresa retiene o percibe a otros (un pasivo a depositar), no lo
# que le retuvieron: no va contra Mis Retenciones.
_DE_OTRA_COSA = ("PRACTICAD", "EFECTUAD", "ADEPOSITAR", "APAGAR", "AINGRESAR", "AORGANISMO")


def tipos_simples(tipo: str) -> list[str]:
    """El tipo de ARCA en tipos de cuenta: el combinado son las dos."""
    return [RETENCION, PERCEPCION] if tipo == COMBINADO else [tipo]


def _normal(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return "".join(c for c in sin_acentos.upper() if c.isalnum())


def puntaje(nombre: str, impuesto: str, tipo: str) -> int:
    """
    Cuanto se parece una cuenta del plan a la de un impuesto y un tipo simple
    (0 = no es). Tiene que nombrar el impuesto y el tipo; suma si dice
    "sufridas" o "a recuperar", que es como se llaman las cuentas de activo.
    """
    n = _normal(nombre)
    if impuesto_de_texto(nombre) != impuesto or _PALABRA_TIPO[tipo] not in n:
        return 0
    if any(p in n for p in _DE_OTRA_COSA):
        return 0
    return 1 + ("SUFRID" in n) + ("ARECUPERAR" in n)


def elegir_cuenta(nombres: list[str], impuesto: str, tipo: str) -> str | None:
    """La cuenta del plan que mejor corresponde a un impuesto y un tipo simple."""
    puntuados = [(puntaje(n, impuesto, tipo), -len(n), n) for n in dict.fromkeys(nombres)]
    puntuados = [p for p in puntuados if p[0] > 0]
    return max(puntuados)[2] if puntuados else None


def elegir_cuentas(nombres: list[str], impuesto: str, tipo: str) -> list[str]:
    """Las cuentas para la consulta de ARCA (una por tipo simple, sin repetir)."""
    elegidas = [elegir_cuenta(nombres, impuesto, t) for t in tipos_simples(tipo)]
    return list(dict.fromkeys(c for c in elegidas if c))


def terminos_de_busqueda(impuesto: str, tipo: str) -> list[str]:
    """
    Lo que se escribe en «Cuenta», en orden de prueba. El buscador de Xubio
    busca por el comienzo del nombre, y las cuentas empiezan por el tipo
    ("Retención Ganancias Sufrida", "Percepción de Ganancias Sufrida"): se
    escribe el tipo y entre lo que lista se elige la del impuesto. Si con eso
    no aparece, se prueba sin acento y despues con el impuesto.
    """
    etiqueta = ETIQUETA_TIPO[tipo]
    sin_acento = unicodedata.normalize("NFKD", etiqueta).encode("ascii", "ignore").decode()
    impuesto_corto = {"Ganancias": "Ganancia", "Ingresos Brutos": "Brutos"}.get(impuesto, impuesto)
    return list(dict.fromkeys([etiqueta, sin_acento, impuesto_corto]))
