"""
Paso 1 de la conciliacion: dejar los dos lados en el mismo formato.

Los importadores ya leen cada archivo con sus vicios, pero el cruce no confia
en eso: vuelve a normalizar cada registro (tambien los que llegan por los bots
o armados a mano) para que las claves se comparen igual de los dos lados.

* fecha        -> date (acepta dd/mm/aaaa, aaaa-mm-dd, etc.).
* CUIT         -> 11 digitos, sin guiones.
* importe      -> numero con dos decimales ("1.234,56" y "1,234.56" valen igual).
* comprobante  -> solo digitos, sin ceros a la izquierda: "0001-00012345" y
                  "000100012345" quedan "100012345".
"""

import re

from app.services.tools import dec, limpiar_numero, normalizar_cuit, parse_fecha, solo_digitos, texto

# Un numero mas corto no identifica un comprobante (un "12" aparece en cualquier glosa).
LARGO_MINIMO_COMPROBANTE = 4


def normalizar_comprobante(valor) -> str:
    """'0001-00012345' -> '100012345'; vacio si no tiene digitos."""
    return solo_digitos(texto(valor)).lstrip("0")


def claves_comprobante(valor) -> set[str]:
    """
    Las formas en que un comprobante puede aparecer escrito del otro lado: el
    numero entero normalizado y cada tramo sin ceros ("0001-00012345" se suele
    cargar "12345" en el Libro Mayor de Xubio). Se compara por numero entero,
    no por substring: el "12345" no tiene que coincidir con una factura "112345".
    """
    tramos = {g.lstrip("0") for g in re.findall(r"\d+", str(texto(valor) or ""))}
    claves = tramos | {normalizar_comprobante(valor)}
    return {c for c in claves if len(c) >= LARGO_MINIMO_COMPROBANTE}


def _importe(valor) -> float:
    numero = valor if isinstance(valor, (int, float)) else limpiar_numero(valor)
    return float(dec(numero))


def _comun(registro: dict) -> dict:
    return {
        **registro,
        "fecha": parse_fecha(registro.get("fecha")),
        "cuit": normalizar_cuit(registro.get("cuit")),
        "importe": _importe(registro.get("importe")),
        "razon_social": texto(registro.get("razon_social")) or "",
        "impuesto": texto(registro.get("impuesto")) or "",
        "tipo": texto(registro.get("tipo")) or "",
    }


def normalizar_arca(registro: dict) -> dict:
    """Una retencion de ARCA; su comprobante es el numero de certificado."""
    normalizado = _comun(registro)
    normalizado["clave_comprobante"] = normalizar_comprobante(registro.get("certificado"))
    return normalizado


def normalizar_mayor(registro: dict) -> dict:
    """
    Un asiento del Libro Mayor de Xubio. El comprobante puede estar en el
    documento o escrito en la descripcion ("Ret. Gcias cert. 12345"): se guardan todas las claves.
    """
    normalizado = _comun(registro)
    normalizado["clave_comprobante"] = normalizar_comprobante(registro.get("documento"))
    normalizado["claves_comprobante"] = claves_comprobante(registro.get("documento")) | claves_comprobante(
        registro.get("descripcion")
    )
    return normalizado
