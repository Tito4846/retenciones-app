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
from app.services.importador_tablas import _normalizar, leer_tabla, normalizar_columnas, valores_originales
from app.services.impuestos import impuesto_de_texto, tipo_de_texto
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

# Filas que traen el saldo del ejercicio anterior, no retenciones del periodo.
MARCAS_SALDO_INICIAL = ("SALDO INICIAL", "SALDO ANTERIOR", "TRANSPORTE", "ASIENTO DE APERTURA", "APERTURA PATRIMONIAL")


class LecturaMayor(NamedTuple):
    retenciones: list[dict]
    # Filas del HABER (aplicaciones/anulaciones): no participan del cruce.
    aplicaciones: list[dict]
    # Saldo inicial y asiento de apertura: tampoco participan, pero van en la
    # hoja LIBRO MAYOR del Excel para que el saldo corrido de igual.
    saldos_iniciales: tuple[dict, ...] = ()


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
    saldos_iniciales: list[dict] = []
    sin_fecha = 0

    for orden, (_, fila) in enumerate(df.iterrows()):
        descripcion = texto(columna(fila, "DETALLE")) or ""
        documento = texto(columna(fila, "DOCUMENTO")) or texto(columna(fila, "COMPROBANTE"))
        cuenta = texto(columna(fila, "CUENTA")) or ""
        # La fila tal cual y su lugar en el mayor, para la hoja LIBRO MAYOR del Excel.
        copia = {"orden": orden, "original": valores_originales(columnas, fila)}

        if any(m in f"{descripcion} {documento or ''}".upper() for m in MARCAS_SALDO_INICIAL):
            saldos_iniciales.append({**copia, "cuenta": cuenta, "impuesto": impuesto_de_texto(cuenta, descripcion) or ""})
            continue

        debe, haber = _importes(fila)
        if debe == 0 and haber == 0:
            continue

        fecha = parse_fecha(columna(fila, "FECHA"))
        if fecha is None:
            # Todo asiento tiene fecha: lo demas son anotaciones a mano o totales.
            sin_fecha += 1
            continue

        razon_social = texto(columna(fila, "RAZON_SOCIAL")) or ""

        movimiento = {
            **copia,
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
            # La cuenta dice si es de retenciones o de percepciones.
            "tipo": tipo_de_texto(cuenta),
        }
        (retenciones if debe else aplicaciones).append(movimiento)

    if not retenciones and not vacio_ok:
        raise ErrorDeLectura(
            "El Libro Mayor no tiene retenciones: ninguna fila con importe en el Debe."
        )
    if sin_fecha:
        logger.warning("Libro Mayor: %d filas sin fecha se saltearon (anotaciones o totales)", sin_fecha)
    logger.info(
        "Libro Mayor: %d retenciones, %d aplicaciones/anulaciones",
        len(retenciones),
        len(aplicaciones),
    )
    return LecturaMayor(retenciones, aplicaciones, tuple(saldos_iniciales))


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
    df = pd.DataFrame(datos, columns=nombres)
    # Solo las columnas que Xubio muestra en pantalla, con su titulo: las mismas
    # del Excel que se exporta (Fecha, Documento, Cuenta, Debe, Haber, Saldo,
    # Descripcion, Organizacion), asi se lee igual que el archivo. Las ocultas
    # confunden: CUIT es el de la empresa y DETALLE no es la descripcion.
    visibles = {
        f.get("name"): f.get("caption") or f.get("name")
        for f in dataset.find("fields").findall("field")
        if (f.get("type") or "").upper() == "VISIBLE"
    }
    if visibles:
        df = df[[n for n in nombres if n in visibles]].rename(columns=visibles)
    return df


def _palabras_de_filtro() -> list[str]:
    return [_normalizar(p) for p in settings.XUBIO_CUENTAS_RETENCIONES.split(",") if p.strip()]


def es_cuenta_de_retenciones(nombre: str) -> bool:
    """'Retenciones IVA Sufridas' -> True con el filtro por defecto."""
    n = _normalizar(unicodedata.normalize("NFKD", nombre or ""))
    return any(p in n for p in _palabras_de_filtro())


def solo_cuentas_de(mayor: LecturaMayor, impuestos: set[str]) -> tuple[LecturaMayor, int]:
    """
    El mayor de la cuenta de retenciones de los `impuestos` consultados en ARCA
    ("Ganancias", "IVA"...), como cuando se baja el mayor de esa sola cuenta.
    Las filas cuyo impuesto no se reconoce se dejan: puede ser la cuenta buscada
    con otro nombre. Devuelve tambien cuantas filas quedaron afuera.
    """
    def es_de_la_cuenta(fila: dict) -> bool:
        return not fila.get("impuesto") or fila["impuesto"] in impuestos

    retenciones = [f for f in mayor.retenciones if es_de_la_cuenta(f)]
    aplicaciones = [f for f in mayor.aplicaciones if es_de_la_cuenta(f)]
    saldos = tuple(f for f in mayor.saldos_iniciales if es_de_la_cuenta(f))
    return LecturaMayor(retenciones, aplicaciones, saldos), len(mayor.retenciones) - len(retenciones)


def solo_cuentas(mayor: LecturaMayor, cuentas: list[str]) -> tuple[LecturaMayor, int]:
    """El mayor de esas cuentas de Xubio y cuantas filas de otras cuentas quedaron afuera."""
    retenciones = [f for f in mayor.retenciones if f.get("cuenta") in cuentas]
    aplicaciones = [f for f in mayor.aplicaciones if f.get("cuenta") in cuentas]
    saldos = tuple(f for f in mayor.saldos_iniciales if f.get("cuenta") in cuentas)
    return LecturaMayor(retenciones, aplicaciones, saldos), len(mayor.retenciones) - len(retenciones)


def unir(partes: list[LecturaMayor]) -> LecturaMayor:
    """
    Los mayores de varias cuentas en uno, una cuenta debajo de la otra: el
    `orden` de cada parte se corre para que en el Excel no se mezclen.
    """
    retenciones, aplicaciones, saldos = [], [], []
    desde = 0
    for parte in partes:
        filas = [*parte.retenciones, *parte.aplicaciones, *parte.saldos_iniciales]
        for destino, origen in ((retenciones, parte.retenciones), (aplicaciones, parte.aplicaciones),
                                (saldos, parte.saldos_iniciales)):
            destino.extend({**f, "orden": desde + f.get("orden", 0)} for f in origen)
        desde += 1 + max((f.get("orden", 0) for f in filas), default=0)
    return LecturaMayor(retenciones, aplicaciones, tuple(saldos))


def leer_mayor_desde_webreport(xml_texto: str, vacio_ok: bool = False, cuenta: str | None = None) -> LecturaMayor:
    """
    El Libro Mayor que bajo el bot. Por defecto se pide el arbol de cuentas
    entero, asi que antes de leer se dejan solo las cuentas de retenciones.
    `vacio_ok`: un periodo sin movimientos de retenciones no es error.
    `cuenta`: la cuenta elegida en la pantalla; el reporte de una sola cuenta
    puede venir sin la columna Cuenta.
    """
    # Se filtra con los nombres canonicos pero a _desde_dataframe van los
    # titulos de Xubio: son los que copia el Excel de salida.
    original = _parsear_webreport(xml_texto)
    if cuenta and not original.empty and "CUENTA" not in normalizar_columnas(original).columns:
        original["Cuenta"] = cuenta
    df = normalizar_columnas(original)
    if df.empty:
        if vacio_ok:
            return LecturaMayor([], [])
        raise ErrorDeLectura("El Libro Mayor de Xubio no tiene movimientos en ese período.")
    if "CUENTA" not in df.columns:
        raise ErrorDeLectura(
            "El Libro Mayor de Xubio vino sin la columna Cuenta: no se pueden separar las "
            "cuentas de retenciones. Configurá XUBIO_CUENTA_REPORT_CODE con la cuenta puntual."
        )

    de_retenciones = original[df["CUENTA"].map(lambda c: es_cuenta_de_retenciones(str(c)))]
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
