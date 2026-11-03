"""
Utilidades de lectura de celdas, traidas del modulo de conciliacion bancaria.

limpiar_numero y parse_fecha son las mismas que usan los extractores de
extractos: los exports de ARCA y de Xubio tienen los mismos vicios (punto de
miles, coma decimal, signos pegados, celdas NaN de pandas).
"""

import re
from datetime import date, datetime
from decimal import Decimal

_RE_SEPARADORES = re.compile(r"[^\d.,-]")
_RE_SOLO_DIGITOS = re.compile(r"\D")

FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d", "%d.%m.%Y")


class ErrorDeLectura(RuntimeError):
    """Un archivo que no se puede usar, con un motivo que el usuario entiende."""


def limpiar_numero(s, es_formato_ingles: bool = False) -> float:
    """
    Convierte un importe escrito como texto a float.

    Acepta las dos convenciones:
      - '1.234,56'  -> 1234.56   (punto de miles, coma decimal)
      - '1,234.56'  -> 1234.56   (coma de miles, punto decimal)

    Sabe manejar signos y simbolos pegados al numero: '-$ 29.000,00' -> -29000.0
    Sin es_formato_ingles el separador decimal es el que aparece mas a la derecha.
    """
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        # pandas mete los vacios como float nan: los tratamos como 'sin valor'
        if s != s:
            return 0.0
        return float(s)

    texto = _RE_SEPARADORES.sub("", str(s).strip())
    if not texto or texto in ("-", "+", ".", ",", "-.", "+."):
        return 0.0

    negativo = texto.startswith("-") or str(s).rstrip().endswith("-")
    texto = texto.lstrip("+-")

    if es_formato_ingles:
        texto = texto.replace(",", "")
    elif "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")

    try:
        valor = float(texto)
    except ValueError:
        digitos = re.sub(r"[^\d.]", "", texto)
        try:
            valor = float(digitos) if digitos not in ("", ".") else 0.0
        except ValueError:
            return 0.0

    return -valor if negativo else valor


def parse_fecha(valor) -> date | None:
    """Devuelve date, o None si la celda no tiene una fecha utilizable."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor

    texto = str(valor).strip()
    if texto.upper() in ("", "S/D", "NAN", "NONE", "NAT", "NULL"):
        return None
    # Excel a veces trae "2026-10-04 00:00:00" como texto.
    texto = texto[:10] if len(texto) > 10 and texto[4] == "-" else texto
    for fmt in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def texto(valor) -> str | None:
    """
    Texto util de una celda del DataFrame, o None si esta vacia.

    pandas mete float('nan') en las celdas vacias y str(nan) == 'nan': sin este
    filtro el papel de trabajo terminaba con la cadena "nan" escrita.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and valor != valor:
        return None
    resultado = str(valor).strip()
    if resultado.lower() in ("nan", "none", "nat"):
        return None
    # Un numero entero que Excel guardo como float: 30712345678.0
    if re.fullmatch(r"\d+\.0", resultado):
        resultado = resultado[:-2]
    return resultado or None


def columna(fila, *nombres):
    """Primer valor presente de una lista de nombres de columna alternativos."""
    for nombre in nombres:
        if nombre in fila.index:
            return fila[nombre]
    return None


def dec(valor) -> Decimal:
    """float a Decimal con dos decimales: el cruce compara al centavo."""
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def solo_digitos(valor) -> str:
    return _RE_SOLO_DIGITOS.sub("", str(valor or ""))


def normalizar_cuit(valor) -> str | None:
    """'30-71234567-8' / 30712345678.0 -> '30712345678', o None si no son 11 digitos."""
    t = texto(valor)
    if not t:
        return None
    digitos = solo_digitos(t)
    return digitos if len(digitos) == 11 else None


def formatear_cuit(cuit: str | None) -> str:
    """'30712345678' -> '30-71234567-8'."""
    if not cuit or len(cuit) != 11:
        return cuit or ""
    return f"{cuit[:2]}-{cuit[2:10]}-{cuit[10]}"


_RE_CUIT_EN_TEXTO = re.compile(r"\b(\d{2})-?(\d{8})-?(\d)\b")


def cuit_en_texto(valor) -> str | None:
    """Primer CUIT que aparezca escrito en un texto libre (descripcion del asiento)."""
    m = _RE_CUIT_EN_TEXTO.search(str(valor or ""))
    return "".join(m.groups()) if m else None
