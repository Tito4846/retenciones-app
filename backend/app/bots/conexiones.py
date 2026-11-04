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
from app.bots.libro_mayor_xubio import ErrorPantallaXubio
from app.bots.navegador_xubio import ErrorXubio, NavegadorXubio, XubioDesconectado
from app.core.config import settings
from app.services.importador_arca import leer_arca
from app.services.cuentas_xubio import elegir_cuentas
from app.services.importador_mayor import LecturaMayor, leer_mayor_desde_webreport, solo_cuentas, solo_cuentas_de
from app.services.importador_mayor import unir as unir_mayores
from app.services.impuestos import impuesto_de_texto
from app.services.tareas import ErrorDeConexion, _leer
from app.services.tools import OperacionCancelada, nunca_cancelada

logger = logging.getLogger(__name__)

arca = NavegadorArca()
xubio = NavegadorXubio()


def estado() -> dict:
    """
    Lo de cada bot, mas el impuesto de cada opcion de ARCA: con eso la
    pantalla dice que cuenta va a buscar el bot en Xubio.
    """
    estado_arca = arca.estado()
    if estado_arca.get("formulario"):
        estado_arca["formulario"] = {
            **estado_arca["formulario"],
            "impuestos": [
                {**i, "impuesto": impuesto_de_texto(i["codigo"], i["descripcion"])}
                for i in estado_arca["formulario"]["impuestos"]
            ],
        }
    return {"arca": estado_arca, "xubio": xubio.estado()}


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
Cancelada = Callable[[], bool]


def _frenar_si(cancelada: Cancelada) -> None:
    if cancelada():
        raise OperacionCancelada()


def traer_automatico(
    cuit_retenido: str,
    desde: date,
    hasta: date,
    impuestos: list[str] | None = None,
    tipo: str = "RetencionPercepcion",
    cuit_agente: str | None = None,
    avance: Avance = lambda paso, detalle: None,
    cancelada: Cancelada = nunca_cancelada,
) -> tuple[list[dict], LecturaMayor]:
    """
    Todo sin archivos, en el orden en que lo haria una persona:

    1. ARCA: Mis Retenciones con la CUIT, el periodo, el impuesto y el tipo.
    2. Xubio: el bot entra a Libro Mayor, pone las mismas fechas (estiradas en
       la tolerancia: el asiento se carga con la fecha del cobro) y en «Cuenta»
       elige la del impuesto y el tipo de ARCA. Lo que muestra Xubio es lo
       que se cruza.
    """
    opciones = (arca.formulario() or {}).get("impuestos", [])
    impuestos = impuestos or misret.impuestos_a_consultar(opciones)
    if not impuestos:
        raise ErrorDeConexion("Elegí el impuesto a consultar en ARCA.")

    consulta = misret.ConsultaArca(
        cuit_retenido=cuit_retenido,
        impuestos=impuestos,
        desde=desde,
        hasta=hasta,
        tipo=tipo,
        cuit_agente=cuit_agente,
    )
    _frenar_si(cancelada)
    avance(PASO_ARCA, f"Consultando Mis Retenciones en ARCA del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}…")
    try:
        descarga = arca.consultar_y_esperar(consulta, lambda detalle: avance(PASO_ARCA, detalle), cancelada)
    except (ArcaDesconectado, misret.ErrorConsulta) as e:
        raise ErrorDeConexion(str(e)) from e
    retenciones = _leer("Retenciones de ARCA", leer_arca, str(descarga.ruta)) if descarga.filas else []

    descripciones = {o["codigo"]: o["descripcion"] for o in opciones}
    nombres = {impuesto_de_texto(c, descripciones.get(c)) for c in impuestos}
    hasta_mayor = hasta + timedelta(days=settings.TOLERANCIA_DIAS)
    _frenar_si(cancelada)
    return retenciones, libro_mayor_de_la_consulta(desde, hasta_mayor, nombres, tipo, avance, cancelada)


def libro_mayor_de_la_consulta(
    desde: date,
    hasta: date,
    impuestos: set[str | None],
    tipo: str,
    avance: Avance = lambda paso, detalle: None,
    cancelada: Cancelada = nunca_cancelada,
) -> LecturaMayor:
    """
    El Libro Mayor de Xubio de las cuentas que corresponden a la consulta de
    ARCA, recorriendo la pantalla. Si la pantalla no responde (cambio el
    diseño de Xubio, falta la direccion), se baja el reporte de todo el plan y
    se eligen las mismas cuentas por nombre: el cruce no se corta.
    """
    try:
        if None in impuestos:
            # Un impuesto que no se sabe a que cuenta va: el mayor entero.
            return _mayor_por_reporte(desde, hasta, avance)
        partes = []
        for impuesto in sorted(impuestos):
            _frenar_si(cancelada)
            leidos = xubio.libro_mayor_por_pantalla(
                desde, hasta, impuesto, tipo, lambda detalle: avance(PASO_XUBIO, detalle)
            )
            for cuenta, xml in leidos:
                partes.append(
                    _leer(
                        f"Libro Mayor de Xubio ({cuenta})",
                        lambda x, c=cuenta: leer_mayor_desde_webreport(x, vacio_ok=True, cuenta=c),
                        xml,
                    )
                )
        return unir_mayores(partes)
    except ErrorPantallaXubio as e:
        logger.warning("Libro Mayor por pantalla: %s. Se usa el reporte.", e)
        _frenar_si(cancelada)
        avance(PASO_XUBIO, f"{e} Sigo con el reporte del Libro Mayor.")
        return cuenta_de_la_consulta(_mayor_por_reporte(desde, hasta, avance), impuestos, tipo)
    except (XubioDesconectado, ErrorXubio) as e:
        raise ErrorDeConexion(str(e)) from e


def _mayor_por_reporte(desde: date, hasta: date, avance: Avance) -> LecturaMayor:
    avance(PASO_XUBIO, f"Bajando el Libro Mayor de Xubio del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}…")
    try:
        xml = xubio.libro_mayor(desde, hasta)
    except (XubioDesconectado, ErrorXubio) as e:
        raise ErrorDeConexion(str(e)) from e
    # Un mes sin retenciones en el mayor no corta el flujo: lo que informe ARCA
    # queda como "falta en el mayor".
    return _leer("Libro Mayor de Xubio", lambda x: leer_mayor_desde_webreport(x, vacio_ok=True), xml)


def cuenta_de_la_consulta(mayor: LecturaMayor, impuestos: set[str | None], tipo: str) -> LecturaMayor:
    """
    Del mayor de todo el plan, las cuentas que se habrian elegido en «Cuenta»:
    las del impuesto y el tipo de ARCA entre las que tiene la empresa. Si no
    hay ninguna con ese nombre, todas las de retenciones del impuesto; con un
    impuesto desconocido, el mayor entero.
    """
    if None in impuestos:
        return mayor
    nombres = [m["cuenta"] for m in mayor.retenciones + mayor.aplicaciones if m.get("cuenta")]
    elegidas = [c for impuesto in sorted(impuestos) for c in elegir_cuentas(nombres, impuesto, tipo)]
    if elegidas:
        filtrado, afuera = solo_cuentas(mayor, elegidas)
        logger.info("Mayor: cuentas %s (%d asientos de otras cuentas quedan fuera)", elegidas, afuera)
        return filtrado
    filtrado, afuera = solo_cuentas_de(mayor, impuestos)
    if afuera:
        logger.info("Mayor: %d asientos de otras cuentas de retenciones quedan fuera (%s)", afuera, sorted(impuestos))
    return filtrado
