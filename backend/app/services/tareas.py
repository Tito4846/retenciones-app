"""
Registro en memoria de las conciliaciones en curso.

El POST devuelve un task_id enseguida y el cruce corre en segundo plano; el
front consulta /estado/{task_id} cada 2 segundos. Vive en memoria del proceso:
con un solo worker de uvicorn alcanza. Si se escala a varios workers hay que
pasarlo a Redis o a la base.
"""

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from app.services.conciliador import conciliar_retenciones
from app.services.importador_arca import leer_arca
from app.services.importador_mayor import LecturaMayor, leer_mayor
from app.services.tools import ErrorDeLectura

logger = logging.getLogger(__name__)

# Los resultados se guardan para poder exportar el Excel despues; pasado este
# tiempo se descartan para no acumular memoria.
VIDA_RESULTADO_SEG = 2 * 60 * 60


@dataclass
class Tarea:
    status: str = "pendiente"  # pendiente | procesando | completado | error
    mensaje: str | None = None
    resultado: dict | None = None
    # Mientras procesa: en que paso va (xubio | arca | cruce) y que esta haciendo.
    paso: str | None = None
    detalle_paso: str | None = None
    creada: float = field(default_factory=time.time)
    # Lo leido de cada lado: con esto se rehace el cruce al emparejar a mano.
    entrada: "Entrada | None" = None
    # {indice ARCA: indice mayor} emparejados a mano.
    manuales: dict[int, int] = field(default_factory=dict)


@dataclass
class Entrada:
    arca: list[dict]
    mayor: LecturaMayor
    hasta_periodo: date | None


_tareas: dict[str, Tarea] = {}
_lock = threading.Lock()


def crear() -> str:
    _limpiar_vencidas()
    task_id = uuid.uuid4().hex
    with _lock:
        _tareas[task_id] = Tarea()
    return task_id


def obtener(task_id: str) -> Tarea | None:
    with _lock:
        return _tareas.get(task_id)


def _actualizar(task_id: str, **cambios) -> None:
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea:
            for clave, valor in cambios.items():
                setattr(tarea, clave, valor)


def avanzar(task_id: str, paso: str, detalle: str | None = None) -> None:
    """Lo que la pantalla muestra mientras la tarea procesa."""
    _actualizar(task_id, paso=paso, detalle_paso=detalle)


def _limpiar_vencidas() -> None:
    limite = time.time() - VIDA_RESULTADO_SEG
    with _lock:
        for task_id in [k for k, t in _tareas.items() if t.creada < limite]:
            del _tareas[task_id]


def procesar(task_id: str, ruta_arca: str, ruta_mayor: str) -> None:
    """Conciliacion con los dos archivos subidos (se borran al terminar)."""

    def cargar() -> tuple[list[dict], LecturaMayor]:
        return _leer("Archivo de ARCA", leer_arca, ruta_arca), _leer("Libro Mayor", leer_mayor, ruta_mayor)

    try:
        procesar_con(task_id, cargar)
    finally:
        for ruta in (ruta_arca, ruta_mayor):
            try:
                os.remove(ruta)
            except OSError:
                pass


def procesar_con(
    task_id: str,
    cargar: Callable[[], tuple[list[dict], LecturaMayor]],
    hasta_periodo: date | None = None,
) -> None:
    """
    Corre `cargar` (archivos subidos o bots de las conexiones), cruza y deja
    el resultado en la tarea. Los errores de lectura llegan a la pantalla tal
    cual; cualquier otro, con un mensaje generico y el detalle en el log.
    """
    _actualizar(task_id, status="procesando")
    try:
        arca, mayor = cargar()
        avanzar(task_id, "cruce", "Cruzando las retenciones de ARCA con el Libro Mayor…")
        entrada = Entrada(arca, mayor, hasta_periodo)
        _actualizar(task_id, status="completado", entrada=entrada, resultado=_cruzar(entrada, {}))
    except (ErrorDeLectura, ErrorDeConexion) as e:
        _actualizar(task_id, status="error", mensaje=str(e))
    except Exception:
        logger.exception("Fallo la conciliacion %s", task_id)
        _actualizar(task_id, status="error", mensaje="Error inesperado al procesar los datos.")


def _cruzar(entrada: Entrada, manuales: dict[int, int]) -> dict:
    resultado = conciliar_retenciones(
        entrada.arca,
        entrada.mayor.retenciones,
        hasta_periodo=entrada.hasta_periodo,
        pares_manuales=manuales,
    )
    resultado["resumen"]["aplicaciones_mayor"] = len(entrada.mayor.aplicaciones)
    return resultado


class ErrorDeEmparejado(ValueError):
    """El par pedido no se puede hacer. El mensaje es para la pantalla."""


def _indice(identificador: str, prefijo: str, largo: int) -> int:
    if identificador[:1] == prefijo and identificador[1:].isdigit() and int(identificador[1:]) < largo:
        return int(identificador[1:])
    raise ErrorDeEmparejado(f"Movimiento desconocido: {identificador}.")


def emparejar(task_id: str, arca_id: str, mayor_id: str) -> dict:
    """
    Concilia a mano una retencion de ARCA con un asiento del mayor (los dos
    tienen que estar sin par) y rehace el cruce. Devuelve el resultado nuevo.
    """
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea is None or tarea.status != "completado" or tarea.entrada is None:
            raise KeyError(task_id)
        i = _indice(arca_id, "a", len(tarea.entrada.arca))
        j = _indice(mayor_id, "m", len(tarea.entrada.mayor.retenciones))
        libres_arca = {f["id"] for f in tarea.resultado["detalle"]["faltan_en_mayor"]}
        libres_mayor = {f["id"] for f in tarea.resultado["detalle"]["faltan_en_arca"]}
        if arca_id not in libres_arca:
            raise ErrorDeEmparejado("Esa retención de ARCA ya está conciliada.")
        if mayor_id not in libres_mayor:
            raise ErrorDeEmparejado("Ese asiento del mayor ya está conciliado o es de otro período.")
        manuales = {**tarea.manuales, i: j}
        tarea.resultado = _cruzar(tarea.entrada, manuales)
        tarea.manuales = manuales
        return tarea.resultado


def desemparejar(task_id: str, arca_id: str) -> dict:
    """Deshace un par hecho a mano y rehace el cruce."""
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea is None or tarea.status != "completado" or tarea.entrada is None:
            raise KeyError(task_id)
        i = _indice(arca_id, "a", len(tarea.entrada.arca))
        if i not in tarea.manuales:
            raise ErrorDeEmparejado("Ese par no se hizo a mano: no se puede deshacer.")
        manuales = {k: v for k, v in tarea.manuales.items() if k != i}
        tarea.resultado = _cruzar(tarea.entrada, manuales)
        tarea.manuales = manuales
        return tarea.resultado


def _leer(etiqueta: str, lector: Callable, origen):
    try:
        return lector(origen)
    except ErrorDeLectura as e:
        raise ErrorDeLectura(f"{etiqueta}: {e}") from e


class ErrorDeConexion(RuntimeError):
    """Un bot de las conexiones no pudo traer los datos. El mensaje va a la pantalla."""
