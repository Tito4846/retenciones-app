import logging
import os
import shutil
import tempfile
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.config import settings
from app.services import exportador, tareas
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
    if tarea.status == "error":
        return {"status": "error", "mensaje": tarea.mensaje}
    return {"status": tarea.status, "paso": tarea.paso, "detalle_paso": tarea.detalle_paso}


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


@router.get("/exportar/{task_id}")
def exportar(task_id: str, empresa: str | None = None) -> Response:
    """El papel de trabajo .xlsx de una conciliacion ya terminada."""
    tarea = tareas.obtener(task_id)
    if tarea is None or tarea.status != "completado":
        raise HTTPException(404, "No hay una conciliación terminada para exportar.")

    contenido = exportador.exportar(tarea.resultado, empresa=empresa)
    nombre = exportador.nombre_archivo(empresa)
    # filename*=UTF-8'' para que los acentos lleguen bien al navegador.
    disposicion = f"attachment; filename=\"retenciones.xlsx\"; filename*=UTF-8''{quote(nombre)}"
    return Response(contenido, media_type=TIPO_XLSX, headers={"Content-Disposition": disposicion})
