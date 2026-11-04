"""
Lectura del export "Mis Retenciones" de ARCA.

Cada fila es una retencion o percepcion que un agente informo a nombre del
contribuyente: CUIT del agente, impuesto, fecha, importe y numero de
certificado. Es el lado "lo que el fisco dice que te retuvieron".
"""

import logging

from app.services.importador_tablas import leer_tabla, normalizar_columnas, valores_originales
from app.services.impuestos import impuesto_de_texto, tipo_de_texto
from app.services.tools import (
    ErrorDeLectura,
    columna,
    limpiar_numero,
    normalizar_cuit,
    parse_fecha,
    texto,
)

logger = logging.getLogger(__name__)


def _es_encabezado_arca(canonicas: set) -> bool:
    return {"FECHA", "IMPORTE"} <= canonicas


def leer_arca(ruta: str) -> list[dict]:
    """
    Devuelve las retenciones como {cuit, razon_social, fecha, importe, impuesto,
    certificado, comprobante, regimen, tipo, original}. La fila de total del pie
    se saltea.

    El importe va en positivo: una percepcion anulada viene en negativo en ARCA
    y se conserva con signo, para que no se empareje con un asiento normal.
    """
    df = leer_tabla(ruta, es_encabezado=_es_encabezado_arca)
    columnas = [str(c) for c in df.columns]
    df = normalizar_columnas(df)

    faltan = {"FECHA", "IMPORTE"} - set(df.columns)
    if faltan:
        raise ErrorDeLectura(
            "El archivo de ARCA no tiene las columnas de "
            f"{' ni '.join(sorted(faltan)).lower()}. Se encontraron: {columnas}."
        )

    retenciones = []
    sin_fecha = 0
    for _, fila in df.iterrows():
        importe = limpiar_numero(columna(fila, "IMPORTE"))
        if importe == 0:
            continue

        fecha = parse_fecha(columna(fila, "FECHA"))
        cuit = normalizar_cuit(columna(fila, "CUIT"))
        razon_social = texto(columna(fila, "RAZON_SOCIAL")) or ""
        if fecha is None and not cuit and not razon_social:
            # La fila de total que ARCA (o la persona) agrega al pie.
            continue
        if fecha is None:
            sin_fecha += 1

        codigo_impuesto = texto(columna(fila, "IMPUESTO"))
        descripcion_impuesto = texto(columna(fila, "DESCRIPCION_IMPUESTO"))
        impuesto = (
            impuesto_de_texto(codigo_impuesto, descripcion_impuesto)
            or descripcion_impuesto
            or codigo_impuesto
            or ""
        )

        retenciones.append(
            {
                "cuit": cuit,
                "razon_social": razon_social,
                "fecha": fecha,
                "importe": round(importe, 2),
                "impuesto": impuesto,
                "certificado": texto(columna(fila, "CERTIFICADO")),
                "comprobante": texto(columna(fila, "COMPROBANTE")),
                "regimen": texto(columna(fila, "REGIMEN")),
                # "Descripción Operación": RETENCION o PERCEPCION.
                "tipo": tipo_de_texto(columna(fila, "DETALLE")),
                # La fila tal cual, para la hoja ARCA del Excel de salida.
                "original": valores_originales(columnas, fila),
            }
        )

    if not retenciones:
        raise ErrorDeLectura("El archivo de ARCA no tiene ninguna retencion con importe.")
    if sin_fecha:
        logger.warning("ARCA: %d retenciones sin fecha reconocible", sin_fecha)
    logger.info("ARCA: %d retenciones leidas", len(retenciones))
    return retenciones
