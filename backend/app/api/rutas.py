import logging
import os
import shutil
import tempfile
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core.config import settings
from app.services import exportador, revisiones, tareas
from app.services.importador_tablas import EXTENSIONES

logger = logging.getLogger(__name__)

router = APIRouter()

TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _guardar_temporal(archivo: UploadFile, etiqueta: str) -> str:
    """Copia la subida a un temporal conservando la extension (decide el lector)."""
    extension = os.path.splitext(archivo.filename or "")[1].lower()
    if extension not in EXTENSIONES:
        raise HTTPException(
            400,
            f"{etiqueta}: formato '{extension or 'sin extensión'}' no soportado. "
            f"Se aceptan {', '.join(sorted(EXTENSIONES))}.",
        )
    archivo.file.seek(0, os.SEEK_END)
    if archivo.file.tell() > settings.MAX_MB_ARCHIVO * 1024 * 1024:
        raise HTTPException(413, f"{etiqueta}: supera los {settings.MAX_MB_ARCHIVO} MB.")
    archivo.file.seek(0)

    descriptor, ruta = tempfile.mkstemp(suffix=extension, prefix="ret_")
    with os.fdopen(descriptor, "wb") as destino:
        shutil.copyfileobj(archivo.file, destino)
    return ruta


@router.post("/conciliar", status_code=202)
def conciliar(
    background: BackgroundTasks,
    archivo_arca: UploadFile = File(...),
    archivo_mayor: UploadFile = File(...),
) -> dict:
    """Recibe los dos archivos y arranca el cruce en segundo plano."""
    ruta_arca = _guardar_temporal(archivo_arca, "Archivo de ARCA")
    try:
        ruta_mayor = _guardar_temporal(archivo_mayor, "Libro Mayor")
    except HTTPException:
        os.remove(ruta_arca)
        raise

    task_id = tareas.crear()
    background.add_task(tareas.procesar, task_id, ruta_arca, ruta_mayor)
    return {"task_id": task_id}


@router.get("/estado/{task_id}")
def estado(task_id: str) -> dict:
    tarea = tareas.obtener(task_id)
    if tarea is None:
        raise HTTPException(404, "La conciliación no existe o ya venció. Volvé a subir los archivos.")
    if tarea.status == "completado":
        return {"status": "completado", **tarea.resultado}
    if tarea.status in ("error", "cancelado"):
        return {"status": tarea.status, "mensaje": tarea.mensaje}
    return {"status": tarea.status, "paso": tarea.paso, "detalle_paso": tarea.detalle_paso}


@router.post("/cancelar/{task_id}")
def cancelar(task_id: str) -> dict:
    """Corta una conciliacion en curso (el bot frena antes del paso siguiente)."""
    if tareas.obtener(task_id) is None:
        raise HTTPException(404, "La conciliación no existe o ya venció.")
    if not tareas.cancelar(task_id):
        raise HTTPException(409, "La conciliación ya terminó: no hay nada que cancelar.")
    return {"status": "cancelado"}


class Par(BaseModel):
    arca_id: str
    mayor_id: str


@router.post("/conciliacion/{task_id}/pares")
def emparejar(task_id: str, par: Par) -> dict:
    """Concilia a mano una retención de ARCA con un asiento del mayor."""
    try:
        resultado = tareas.emparejar(task_id, par.arca_id, par.mayor_id)
    except KeyError:
        raise HTTPException(404, "La conciliación no existe o ya venció. Volvé a traer los datos.") from None
    except tareas.ErrorDeEmparejado as e:
        raise HTTPException(409, str(e)) from e
    return {"status": "completado", **resultado}


@router.delete("/conciliacion/{task_id}/pares/{arca_id}")
def desemparejar(task_id: str, arca_id: str) -> dict:
    """Deshace un par hecho a mano."""
    try:
        resultado = tareas.desemparejar(task_id, arca_id)
    except KeyError:
        raise HTTPException(404, "La conciliación no existe o ya venció. Volvé a traer los datos.") from None
    except tareas.ErrorDeEmparejado as e:
        raise HTTPException(409, str(e)) from e
    return {"status": "completado", **resultado}


class MovimientoNuevo(BaseModel):
    lado: Literal["arca", "mayor"]
    fecha: str
    razon_social: str = ""
    cuit: str | None = None
    impuesto: str = ""
    tipo: str = "Retencion"
    comprobante: str | None = None
    importe: float


class Cambios(BaseModel):
    # {id de movimiento: {campo: valor}}
    cambios: dict[str, dict[str, str | float | None]] = {}
    nuevos: list[MovimientoNuevo] = []
    # Ids de los movimientos a quitar.
    eliminados: list[str] = []


@router.post("/conciliacion/{task_id}/movimientos")
def guardar_movimientos(task_id: str, datos: Cambios) -> dict:
    """Guarda lo corregido, agregado y quitado a mano en la pantalla y rehace el cruce."""
    try:
        resultado = tareas.guardar_cambios(task_id, datos.cambios, [n.model_dump() for n in datos.nuevos], datos.eliminados)
    except KeyError:
        raise HTTPException(404, "La conciliación no existe o ya venció. Volvé a traer los datos.") from None
    except tareas.ErrorDeEmparejado as e:
        raise HTTPException(422, str(e)) from e
    return {"status": "completado", **resultado}


@router.get("/exportar/{task_id}")
def exportar(task_id: str, empresa: str | None = None) -> Response:
    """El papel de trabajo .xlsx de una conciliacion ya terminada."""
    tarea = tareas.obtener(task_id)
    if tarea is None or tarea.status != "completado":
        raise HTTPException(404, "No hay una conciliación terminada para exportar.")

    contenido = exportador.exportar(tarea.resultado, tarea.entrada)
    nombre = exportador.nombre_archivo(empresa)
    # filename*=UTF-8'' para que los acentos lleguen bien al navegador.
    disposicion = f"attachment; filename=\"retenciones.xlsx\"; filename*=UTF-8''{quote(nombre)}"
    return Response(contenido, media_type=TIPO_XLSX, headers={"Content-Disposition": disposicion})


# ----------------------------------------------------------------------
# Bandeja de revisión
# ----------------------------------------------------------------------


class Envio(BaseModel):
    empresa: str = ""
    periodo: str = ""
    enviado_por: str = ""
    comentario: str = ""


class CambioEstado(BaseModel):
    estado: str
    autor: str = ""
    comentario: str = ""


class Control(BaseModel):
    ok: bool
    autor: str = ""


class Observacion(BaseModel):
    texto: str
    origen: str = "nuestro"
    autor: str = ""


class Resolucion(BaseModel):
    resuelta: bool = True
    autor: str = ""


def _revision(funcion, *args) -> dict:
    try:
        return funcion(*args)
    except KeyError:
        raise HTTPException(404, "La revisión no existe.") from None
    except revisiones.ErrorDeRevision as e:
        raise HTTPException(409, str(e)) from e


@router.post("/revisiones/{task_id}", status_code=201)
def enviar_a_revision(task_id: str, envio: Envio) -> dict:
    """Genera el papel de trabajo de la conciliación y lo deja en la bandeja de revisión."""
    tarea = tareas.obtener(task_id)
    if tarea is None or tarea.status != "completado":
        raise HTTPException(404, "No hay una conciliación terminada para enviar.")
    contenido = exportador.exportar(tarea.resultado, tarea.entrada)
    return revisiones.crear(contenido, exportador.nombre_archivo(envio.empresa or None), tarea.resultado,
                            envio.empresa, envio.periodo, envio.enviado_por, envio.comentario)


@router.get("/revisiones")
def listar_revisiones() -> dict:
    return {"revisiones": revisiones.listar(), "controles": revisiones.CONTROLES}


@router.get("/revisiones/{revision_id}/excel")
def excel_revision(revision_id: str) -> FileResponse:
    revision = revisiones.obtener(revision_id)
    if revision is None or not revisiones.ruta_excel(revision_id).exists():
        raise HTTPException(404, "La revisión no existe.")
    return FileResponse(revisiones.ruta_excel(revision_id), media_type=TIPO_XLSX, filename=revision["archivo"])


@router.post("/revisiones/{revision_id}/estado")
def estado_revision(revision_id: str, cambio: CambioEstado) -> dict:
    return _revision(revisiones.cambiar_estado, revision_id, cambio.estado, cambio.autor, cambio.comentario)


@router.post("/revisiones/{revision_id}/controles/{control}")
def control_revision(revision_id: str, control: str, datos: Control) -> dict:
    return _revision(revisiones.marcar_control, revision_id, control, datos.ok, datos.autor)


@router.post("/revisiones/{revision_id}/observaciones")
def observar_revision(revision_id: str, datos: Observacion) -> dict:
    return _revision(revisiones.observar, revision_id, datos.texto, datos.origen, datos.autor)


@router.post("/revisiones/{revision_id}/observaciones/{observacion_id}")
def resolver_revision(revision_id: str, observacion_id: str, datos: Resolucion) -> dict:
    return _revision(revisiones.resolver_observacion, revision_id, observacion_id, datos.resuelta, datos.autor)


@router.delete("/revisiones/{revision_id}", status_code=204)
def eliminar_revision(revision_id: str) -> None:
    _revision(revisiones.eliminar, revision_id)
