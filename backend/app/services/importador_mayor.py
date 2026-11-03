"""
Lectura del Libro Mayor de Xubio de las cuentas de retenciones sufridas.

Adaptado del importador del modulo bancario. En una cuenta de retenciones
sufridas (activo) cada retencion que nos hicieron entra por el DEBE; el HABER
es la aplicacion del saldo a la DDJJ o una anulacion, y no tiene par en ARCA.
Por eso solo las filas con DEBE son candidatas al cruce: las de HABER se
cuentan y se devuelven aparte para que no queden como "falta certificado".
"""

import logging
import unicodedata
import xml.etree.ElementTree as ET
from typing import NamedTuple

import pandas as pd

from app.core.config import settings
from app.services.importador_tablas import _normalizar, leer_tabla, normalizar_columnas
from app.services.impuestos import impuesto_de_texto
from app.services.tools import (
    ErrorDeLectura,
    columna,
    cuit_en_texto,
    limpiar_numero,
    normalizar_cuit,
    parse_fecha,
    texto,
)

logger = logging.getLogger(__name__)

MARCAS_SALDO_INICIAL = ("SALDO INICIAL", "SALDO ANTERIOR", "TRANSPORTE")


class LecturaMayor(NamedTuple):
    retenciones: list[dict]
    # Filas del HABER (aplicaciones/anulaciones): no participan del cruce.
    aplicaciones: list[dict]


def _es_encabezado_de_mayor(canonicas: set) -> bool:
    """FECHA mas alguna columna de importe (el mayor puede venir sin detalle)."""
    return "FECHA" in canonicas and bool(canonicas & {"DEBE", "HABER", "IMPORTE"})


def _importes(fila) -> tuple[float, float]:
    """
    (debe, haber) en positivo. Con una sola columna IMPORTE con signo, el
    positivo es DEBE y el negativo HABER.
    """
    debe = abs(limpiar_numero(columna(fila, "DEBE")))
    haber = abs(limpiar_numero(columna(fila, "HABER")))
    if debe == 0 and haber == 0:
        valor = limpiar_numero(columna(fila, "IMPORTE"))
        if valor > 0:
            debe = valor
        elif valor < 0:
            haber = -valor
    return debe, haber


def leer_mayor(ruta: str) -> LecturaMayor:
    """El Libro Mayor exportado a archivo (xlsx/csv) desde Xubio."""
    return _desde_dataframe(leer_tabla(ruta, es_encabezado=_es_encabezado_de_mayor))


def _desde_dataframe(df: pd.DataFrame, vacio_ok: bool = False) -> LecturaMayor:
    """
    Convierte las filas del mayor en retenciones. Lo comparten el archivo y el
    webreport del bot: un solo traductor de columnas para las dos fuentes.
    `vacio_ok`: sin retenciones devuelve listas vacias en vez de error.
    """
    columnas = [str(c) for c in df.columns]
    df = normalizar_columnas(df)

    if "FECHA" not in df.columns:
        raise ErrorDeLectura(
            f"El Libro Mayor no tiene columna de Fecha. Se encontraron: {columnas}."
        )
    if not ({"DEBE", "HABER", "IMPORTE"} & set(df.columns)):
        raise ErrorDeLectura(
            "El Libro Mayor no tiene columnas de importe (Debe, Haber ni Importe). "
            f"Se encontraron: {columnas}."
        )

    retenciones: list[dict] = []
    aplicaciones: list[dict] = []
    sin_fecha = 0

    for _, fila in df.iterrows():
        descripcion = texto(columna(fila, "DETALLE")) or ""
        if any(m in descripcion.upper() for m in MARCAS_SALDO_INICIAL):
            continue

        debe, haber = _importes(fila)
        if debe == 0 and haber == 0:
            continue

        fecha = parse_fecha(columna(fila, "FECHA"))
        if fecha is None:
            sin_fecha += 1

        cuenta = texto(columna(fila, "CUENTA")) or ""
        documento = texto(columna(fila, "DOCUMENTO")) or texto(columna(fila, "COMPROBANTE"))
        razon_social = texto(columna(fila, "RAZON_SOCIAL")) or ""

        movimiento = {
            "fecha": fecha,
            "importe": round(debe if debe else haber, 2),
            "cuenta": cuenta,
            "documento": documento,
            "descripcion": descripcion,
            "razon_social": razon_social,
            # Xubio no exporta el CUIT en el mayor, pero a veces viene escrito
            # en la descripcion del asiento.
            "cuit": normalizar_cuit(columna(fila, "CUIT")) or cuit_en_texto(descripcion),
            "impuesto": impuesto_de_texto(cuenta, descripcion) or "",
        }
        (retenciones if debe else aplicaciones).append(movimiento)

    if not retenciones and not vacio_ok:
        raise ErrorDeLectura(
            "El Libro Mayor no tiene retenciones: ninguna fila con importe en el Debe."
        )
    if sin_fecha:
        logger.warning("Libro Mayor: %d filas sin fecha reconocible", sin_fecha)
    logger.info(
        "Libro Mayor: %d retenciones, %d aplicaciones/anulaciones",
        len(retenciones),
        len(aplicaciones),
    )
    return LecturaMayor(retenciones, aplicaciones)


# ----------------------------------------------------------------------
# El mismo mayor, pero bajado por el bot de Xubio (webreport)
# ----------------------------------------------------------------------
#
# El webreport de Xubio no devuelve JSON sino XML:
#   <root><dataset><fields><field name=.../></fields><data>CSV</data></dataset></root>
# con las filas separadas por ';' y los campos por ',', y un PK al principio de
# cada fila. Se arma un DataFrame y se reusa _desde_dataframe.


def _parsear_webreport(xml_texto: str) -> pd.DataFrame:
    try:
        raiz = ET.fromstring(xml_texto)
    except ET.ParseError as error:
        raise ErrorDeLectura(
            "Xubio no devolvió el Libro Mayor en el formato esperado: " + " ".join(xml_texto.split())[:300]
        ) from error
    dataset = raiz if raiz.tag == "dataset" else raiz.find(".//dataset")
    if dataset is None or dataset.find("fields") is None:
        raise ErrorDeLectura("Xubio no devolvió el Libro Mayor: " + " ".join(xml_texto.split())[:300])

    nombres = [f.get("name") for f in dataset.find("fields").findall("field")]
    data = dataset.find("data")
    filas = [r for r in (data.text or "").split(";") if r] if data is not None else []

    esperado = len(nombres) + 1
    datos, descartadas = [], 0
    for fila in filas:
        partes = fila.split(",")
        if len(partes) != esperado:
            descartadas += 1
            continue
        datos.append(partes[1:])  # sin el PK
    if descartadas:
        logger.warning("Webreport: %d filas con cantidad de campos inesperada se descartaron", descartadas)
    return pd.DataFrame(datos, columns=nombres)


def _palabras_de_filtro() -> list[str]:
    return [_normalizar(p) for p in settings.XUBIO_CUENTAS_RETENCIONES.split(",") if p.strip()]


def es_cuenta_de_retenciones(nombre: str) -> bool:
    """'Retenciones IVA Sufridas' -> True con el filtro por defecto."""
    n = _normalizar(unicodedata.normalize("NFKD", nombre or ""))
    return any(p in n for p in _palabras_de_filtro())


def leer_mayor_desde_webreport(xml_texto: str, vacio_ok: bool = False) -> LecturaMayor:
    """
    El Libro Mayor que bajo el bot. Por defecto se pide el arbol de cuentas
    entero, asi que antes de leer se dejan solo las cuentas de retenciones.
    `vacio_ok`: un periodo sin movimientos de retenciones no es error.
    """
    df = normalizar_columnas(_parsear_webreport(xml_texto))
    if df.empty:
        if vacio_ok:
            return LecturaMayor([], [])
        raise ErrorDeLectura("El Libro Mayor de Xubio no tiene movimientos en ese período.")
    if "CUENTA" not in df.columns:
        raise ErrorDeLectura(
            "El Libro Mayor de Xubio vino sin la columna Cuenta: no se pueden separar las "
            "cuentas de retenciones. Configurá XUBIO_CUENTA_REPORT_CODE con la cuenta puntual."
        )

    de_retenciones = df[df["CUENTA"].map(lambda c: es_cuenta_de_retenciones(str(c)))]
    if de_retenciones.empty and vacio_ok:
        return LecturaMayor([], [])
    if de_retenciones.empty:
        raise ErrorDeLectura(
            "El Libro Mayor de Xubio no tiene movimientos en cuentas de retenciones "
            f"(se buscan cuentas que digan: {settings.XUBIO_CUENTAS_RETENCIONES}). "
            "Ajustá XUBIO_CUENTAS_RETENCIONES en el .env con el nombre de tus cuentas."
        )
    logger.info("Webreport: %d de %d filas son de cuentas de retenciones", len(de_retenciones), len(df))
    return _desde_dataframe(de_retenciones, vacio_ok=vacio_ok)
