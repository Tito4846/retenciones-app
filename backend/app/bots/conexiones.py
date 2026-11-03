"""
Las dos conexiones del proceso (una ventana de ARCA y una de Xubio por
servidor) y como se combinan para conciliar sin subir archivos.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta

from app.bots import misret
from app.bots.navegador_arca import ArcaDesconectado, NavegadorArca
from app.bots.navegador_xubio import ErrorXubio, NavegadorXubio, XubioDesconectado
from app.core.config import settings
from app.services.importador_arca import leer_arca
from app.services.importador_mayor import LecturaMayor, leer_mayor_desde_webreport
from app.services.tareas import ErrorDeConexion, _leer

logger = logging.getLogger(__name__)

arca = NavegadorArca()
xubio = NavegadorXubio()


def estado() -> dict:
    return {"arca": arca.estado(), "xubio": xubio.estado()}


def periodo_del_mayor(retenciones: list[dict], desde: date | None, hasta: date | None) -> tuple[date, date]:
    """
    Fechas para pedir el Libro Mayor: las que mando la pantalla o, si no, las
    de las retenciones de ARCA estiradas en la tolerancia del cruce (el asiento
    puede estar unos dias despues del certificado).
    """
    fechas = [r["fecha"] for r in retenciones if r.get("fecha")]
    margen = timedelta(days=settings.TOLERANCIA_DIAS)
    if desde is None:
        desde = (min(fechas) - margen) if fechas else date.today().replace(day=1)
    if hasta is None:
        hasta = (max(fechas) + margen) if fechas else date.today()
    return desde, hasta


def cargar_desde_conexiones(desde: date | None = None, hasta: date | None = None) -> tuple[list[dict], LecturaMayor]:
    """El archivo que exporto la persona en la ventana de ARCA + el mayor bajado por el bot de Xubio."""
    descarga = arca.ultima_descarga()
    if descarga is None or not descarga.ruta.exists():
        raise ErrorDeConexion(
            "Todavía no hay retenciones de ARCA: en Conexiones, abrí la ventana de ARCA y tocá «Exportar» en Mis Retenciones."
        )
    retenciones = _leer("Archivo de ARCA", leer_arca, str(descarga.ruta))

    desde, hasta = periodo_del_mayor(retenciones, desde, hasta)
    logger.info("Pidiendo el Libro Mayor a Xubio del %s al %s", desde, hasta)
    try:
        xml = xubio.libro_mayor(desde, hasta)
    except (XubioDesconectado, ErrorXubio) as e:
        raise ErrorDeConexion(str(e)) from e
    return retenciones, _leer("Libro Mayor de Xubio", leer_mayor_desde_webreport, xml)


# Pasos del flujo automatico, en el orden en que corren (la pantalla los dibuja).
PASO_XUBIO = "xubio"
PASO_ARCA = "arca"

Avance = Callable[[str, str], None]


def traer_automatico(
    cuit_retenido: str,
    desde: date,
    hasta: date,
    impuestos: list[str] | None = None,
    tipo: str = "RetencionPercepcion",
    cuit_agente: str | None = None,
    avance: Avance = lambda paso, detalle: None,
) -> tuple[list[dict], LecturaMayor]:
    """
    Todo sin archivos, en un solo paso:

    1. El bot de Xubio baja el Libro Mayor del periodo, estirado en la
       tolerancia (el asiento se carga con la fecha del cobro).
    2. Si no se eligieron impuestos, se deducen de las cuentas de retenciones
       que tienen movimientos en ese mayor (IVA, Ganancias...).
    3. El bot de ARCA consulta Mis Retenciones con cada impuesto.
    """
    hasta_mayor = hasta + timedelta(days=settings.TOLERANCIA_DIAS)
    avance(PASO_XUBIO, f"Bajando el Libro Mayor de Xubio del {desde:%d/%m/%Y} al {hasta_mayor:%d/%m/%Y}…")
    try:
        xml = xubio.libro_mayor(desde, hasta_mayor)
    except (XubioDesconectado, ErrorXubio) as e:
        raise ErrorDeConexion(str(e)) from e
    # Un mes sin retenciones en el mayor no corta el flujo: lo que informe ARCA
    # queda como "no declarado".
    mayor = _leer("Libro Mayor de Xubio", lambda x: leer_mayor_desde_webreport(x, vacio_ok=True), xml)

    if not impuestos:
        detectados = {m["impuesto"] for m in mayor.retenciones if m["impuesto"]}
        opciones = (arca.formulario() or {}).get("impuestos", [])
        impuestos = misret.impuestos_a_consultar(opciones, detectados)
        if not impuestos:
            raise ErrorDeConexion(
                "No se pudo deducir qué impuestos consultar en ARCA: elegilos a mano en la consulta."
            )
        logger.info("Impuestos detectados en el mayor %s -> consulta ARCA %s", sorted(detectados), impuestos)

    consulta = misret.ConsultaArca(
        cuit_retenido=cuit_retenido,
        impuestos=impuestos,
        desde=desde,
        hasta=hasta,
        tipo=tipo,
        cuit_agente=cuit_agente,
    )
    avance(PASO_ARCA, f"Consultando Mis Retenciones en ARCA ({len(impuestos)} impuesto/s)…")
    try:
        descarga = arca.consultar_y_esperar(consulta, lambda detalle: avance(PASO_ARCA, detalle))
    except (ArcaDesconectado, misret.ErrorConsulta) as e:
        raise ErrorDeConexion(str(e)) from e

    retenciones = _leer("Retenciones de ARCA", leer_arca, str(descarga.ruta)) if descarga.filas else []
    return retenciones, mayor
