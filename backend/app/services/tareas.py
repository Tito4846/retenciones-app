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
from app.services.importador_tablas import columna_canonica
from app.services.tools import ErrorDeLectura, OperacionCancelada, normalizar_cuit, parse_fecha

logger = logging.getLogger(__name__)

# Los resultados se guardan para poder exportar el Excel despues; pasado este
# tiempo se descartan para no acumular memoria.
VIDA_RESULTADO_SEG = 2 * 60 * 60


@dataclass
class Tarea:
    status: str = "pendiente"  # pendiente | procesando | completado | error | cancelado
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
    # La persona toco «Cancelar»: los bots lo miran entre paso y paso.
    cancelada: threading.Event = field(default_factory=threading.Event)


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


def _terminar(task_id: str, **cambios) -> None:
    """Deja el resultado final, salvo que la tarea ya se haya cancelado."""
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea and not tarea.cancelada.is_set():
            for clave, valor in cambios.items():
                setattr(tarea, clave, valor)


def cancelar(task_id: str) -> bool:
    """
    Marca la tarea como cancelada. El bot no se corta en el medio de un clic:
    termina lo que esta haciendo y frena antes del paso siguiente. False si la
    tarea no existe o ya termino.
    """
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea is None or tarea.status not in ("pendiente", "procesando"):
            return False
        tarea.cancelada.set()
        tarea.status, tarea.mensaje = "cancelado", "Operación cancelada."
    logger.info("Conciliacion %s cancelada", task_id)
    return True


def cancelada(task_id: str) -> bool:
    tarea = obtener(task_id)
    return tarea is not None and tarea.cancelada.is_set()


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
    _terminar(task_id, status="procesando")
    try:
        arca, mayor = cargar()
        if cancelada(task_id):
            return
        avanzar(task_id, "cruce", "Cruzando las retenciones de ARCA con el Libro Mayor…")
        entrada = Entrada(arca, mayor, hasta_periodo)
        _terminar(task_id, status="completado", entrada=entrada, resultado=_cruzar(entrada, {}))
    except OperacionCancelada:
        pass  # el estado ya dice «cancelado»
    except (ErrorDeLectura, ErrorDeConexion) as e:
        _terminar(task_id, status="error", mensaje=str(e))
    except Exception:
        logger.exception("Fallo la conciliacion %s", task_id)
        _terminar(task_id, status="error", mensaje="Error inesperado al procesar los datos.")


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


# Campo de la pantalla -> columna canonica de la planilla original (para el Excel).
_COLUMNAS_ARCA = {"fecha": "FECHA", "razon_social": "RAZON_SOCIAL", "cuit": "CUIT", "certificado": "CERTIFICADO", "importe": "IMPORTE"}
_COLUMNAS_MAYOR = {"fecha": "FECHA", "razon_social": "RAZON_SOCIAL", "documento": "DOCUMENTO", "importe": "DEBE"}


def _aplicar(registro: dict, campos: dict, columnas: dict[str, str]) -> None:
    """Pisa los datos leidos y la fila original (la que copia el Excel) con lo editado."""
    for campo, valor in campos.items():
        if campo not in columnas:
            continue
        if campo == "fecha":
            valor = parse_fecha(valor)
            if valor is None:
                raise ErrorDeEmparejado("Hay una fecha que no es válida.")
        elif campo == "importe":
            valor = round(float(valor), 2)
            if valor <= 0:
                raise ErrorDeEmparejado("El importe tiene que ser mayor a 0.")
        elif campo == "cuit":
            valor = normalizar_cuit(valor)
        else:
            valor = (str(valor).strip() or None) if valor is not None else None
        registro[campo] = valor if valor is not None or campo != "razon_social" else ""
        original = registro.get("original")
        if original:
            for encabezado in original:
                if columna_canonica(encabezado) == columnas[campo]:
                    original[encabezado] = valor
                    break


def _fila_vacia(filas: list[dict]) -> dict | None:
    """Una fila original con los mismos encabezados y sin valores, para un movimiento nuevo."""
    modelo = next((f["original"] for f in filas if f.get("original")), None)
    return dict.fromkeys(modelo) if modelo else None


def guardar_cambios(task_id: str, cambios: dict[str, dict], nuevos: list[dict], eliminados: list[str] = ()) -> dict:
    """
    Aplica lo corregido a mano en la pantalla (por id de movimiento) y los
    movimientos agregados y quitados, y rehace el cruce: el resumen, el detalle
    y el Excel salen ya con los cambios. Si se quita algo, los ids de lo que
    sigue se corren (el resultado nuevo ya viene con los ids nuevos).
    """
    with _lock:
        tarea = _tareas.get(task_id)
        if tarea is None or tarea.status != "completado" or tarea.entrada is None:
            raise KeyError(task_id)
        entrada = tarea.entrada
        # Se trabaja sobre copias: si algo falla, la conciliacion queda como estaba.
        arca = [{**r, "original": dict(r["original"]) if r.get("original") else None} for r in entrada.arca]
        retenciones = [{**m, "original": dict(m["original"]) if m.get("original") else None} for m in entrada.mayor.retenciones]

        for identificador, campos in cambios.items():
            if identificador.startswith("a"):
                _aplicar(arca[_indice(identificador, "a", len(arca))], campos, _COLUMNAS_ARCA)
            else:
                _aplicar(retenciones[_indice(identificador, "m", len(retenciones))], campos, _COLUMNAS_MAYOR)

        for nuevo in nuevos:
            impuesto = (nuevo.get("impuesto") or "").strip()
            tipo = nuevo.get("tipo") if nuevo.get("tipo") in ("Retencion", "Percepcion") else "Retencion"
            if nuevo.get("lado") == "arca":
                registro = {"impuesto": impuesto, "tipo": tipo, "regimen": None, "comprobante": None,
                            "original": _fila_vacia(arca)}
                campos = {k: nuevo.get(k) for k in ("fecha", "razon_social", "cuit", "importe")}
                campos["certificado"] = nuevo.get("comprobante")
                _aplicar(registro, campos, _COLUMNAS_ARCA)
                arca.append(registro)
            else:
                # La cuenta de un asiento del mismo impuesto y tipo, o una con ese nombre.
                cuenta = next(
                    (m["cuenta"] for m in retenciones if m.get("impuesto") == impuesto and m.get("tipo") == tipo and m.get("cuenta")),
                    f"{'Percepciones' if tipo == 'Percepcion' else 'Retenciones'} {impuesto} sufridas".replace("  ", " "),
                )
                orden = max((m.get("orden", 0) for m in [*retenciones, *entrada.mayor.aplicaciones]), default=0) + 1
                registro = {"orden": orden, "cuenta": cuenta, "descripcion": "Agregado a mano", "cuit": None,
                            "impuesto": impuesto, "tipo": tipo, "original": _fila_vacia(retenciones)}
                if registro["original"] is not None:
                    for encabezado in registro["original"]:
                        canonica = columna_canonica(encabezado)
                        if canonica == "CUENTA":
                            registro["original"][encabezado] = cuenta
                        elif canonica == "DETALLE":
                            registro["original"][encabezado] = "Agregado a mano"
                campos = {k: nuevo.get(k) for k in ("fecha", "razon_social", "importe")}
                campos["documento"] = nuevo.get("comprobante")
                _aplicar(registro, campos, _COLUMNAS_MAYOR)
                retenciones.append(registro)

        # Quitar: los pares hechos a mano se reacomodan a los indices nuevos, y
        # los que usaban un movimiento quitado se pierden.
        quitar_arca = {_indice(i, "a", len(entrada.arca)) for i in eliminados if i.startswith("a")}
        quitar_mayor = {_indice(i, "m", len(entrada.mayor.retenciones)) for i in eliminados if i.startswith("m")}
        nuevo_arca = {v: n for n, v in enumerate(i for i in range(len(arca)) if i not in quitar_arca)}
        nuevo_mayor = {v: n for n, v in enumerate(j for j in range(len(retenciones)) if j not in quitar_mayor)}
        arca = [r for i, r in enumerate(arca) if i in nuevo_arca]
        retenciones = [m for j, m in enumerate(retenciones) if j in nuevo_mayor]
        manuales = {nuevo_arca[i]: nuevo_mayor[j] for i, j in tarea.manuales.items() if i in nuevo_arca and j in nuevo_mayor}

        nueva = Entrada(arca, entrada.mayor._replace(retenciones=retenciones), entrada.hasta_periodo)
        tarea.resultado = _cruzar(nueva, manuales)
        tarea.entrada = nueva
        tarea.manuales = manuales
        return tarea.resultado


def _leer(etiqueta: str, lector: Callable, origen):
    try:
        return lector(origen)
    except ErrorDeLectura as e:
        raise ErrorDeLectura(f"{etiqueta}: {e}") from e


class ErrorDeConexion(RuntimeError):
    """Un bot de las conexiones no pudo traer los datos. El mensaje va a la pantalla."""
