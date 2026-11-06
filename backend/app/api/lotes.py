"""API de lotes de conciliación (Maker-Checker). Ver docs/diseno-revision.md."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.db import transaccion
from app.core.usuarios import Usuario, usuario_actual
from app.services import auditoria, lotes, tareas

router = APIRouter(tags=["lotes"])
UsuarioActual = Annotated[Usuario, Depends(usuario_actual)]
Evidencia = Annotated[list[UploadFile], File()]


async def _leer(evidencia: list[UploadFile]) -> list[tuple[str, bytes]]:
    return [(a.filename or "comprobante", await a.read()) for a in evidencia]


class NuevoLote(BaseModel):
    task_id: str
    empresa: str = ""
    cuit_empresa: str = ""
    periodo: str = ""
    comentario: str = ""


class Rectificacion(BaseModel):
    task_id: str
    motivo: str = ""


class ConVersion(BaseModel):
    version: int


class Regeneracion(BaseModel):
    task_id: str
    version_base: int


class CambioPartida(ConVersion):
    justificacion: str | None = None
    comentario: str | None = None
    # Mandar null borra el ajuste: importa si vino o no, no solo el valor.
    importe_ajustado: str | None = None
    excluida: bool | None = None


class MarcaControl(ConVersion):
    ok: bool


class NuevaObservacion(ConVersion):
    texto: str
    origen: Literal["nuestro", "cliente", "arca"] = "nuestro"
    clave_partida: str | None = None


class Resolucion(BaseModel):
    resuelta: bool = True


class ObservacionRechazo(BaseModel):
    texto: str
    origen: Literal["nuestro", "cliente", "arca"] = "nuestro"
    clave: str | None = None


class Rechazo(ConVersion):
    motivo: str
    observaciones: list[ObservacionRechazo] = Field(default_factory=list)


class Anulacion(BaseModel):
    motivo: str


class PedidoExportacion(BaseModel):
    tipo: str


@router.get("/lotes/catalogo")
def catalogo() -> dict:
    return lotes.catalogo()


@router.get("/lotes")
def listar(_: UsuarioActual, estado: str | None = None, periodo: str | None = None, texto: str | None = None) -> dict:
    return {"lotes": lotes.listar(estado, periodo, texto)}


@router.post("/lotes", status_code=201)
def crear(usuario: UsuarioActual, datos: NuevoLote) -> dict:
    return lotes.crear(usuario, tareas.obtener(datos.task_id), datos.empresa, datos.cuit_empresa, datos.periodo, datos.comentario)


class DesdeCruce(BaseModel):
    task_id: str


@router.post("/lotes/desde-cruce", status_code=201)
def desde_cruce(usuario: UsuarioActual, datos: DesdeCruce) -> dict:
    return lotes.desde_cruce(usuario, tareas.obtener(datos.task_id))


@router.get("/lotes/{lote_id}")
def detalle(_: UsuarioActual, lote_id: str) -> dict:
    return lotes.detalle(lote_id)


@router.post("/lotes/{lote_id}/rectificar", status_code=201)
def rectificar(usuario: UsuarioActual, lote_id: str, datos: Rectificacion) -> dict:
    return lotes.crear(usuario, tareas.obtener(datos.task_id), "", "", "", datos.motivo, rectifica_a=lote_id)


# --- Versiones -------------------------------------------------------------


@router.post("/lotes/{lote_id}/versiones", status_code=201)
async def subir_version(usuario: UsuarioActual, lote_id: str, archivo: Annotated[UploadFile, File()],
                        version_base: Annotated[int, Form()]) -> dict:
    contenido = await archivo.read()
    return lotes.subir_version(usuario, lote_id, contenido, archivo.filename or "version.xlsx", version_base)


@router.post("/lotes/{lote_id}/versiones/regenerar", status_code=201)
def regenerar(usuario: UsuarioActual, lote_id: str, datos: Regeneracion) -> dict:
    return lotes.regenerar(usuario, lote_id, tareas.obtener(datos.task_id), datos.version_base)


@router.get("/lotes/{lote_id}/versiones/{numero}/excel")
def excel(usuario: UsuarioActual, lote_id: str, numero: int) -> FileResponse:
    ruta, nombre = lotes.archivo_version(usuario, lote_id, numero)
    return FileResponse(ruta, media_type=lotes.TIPO_XLSX, filename=nombre)


@router.get("/lotes/{lote_id}/versiones/{numero}/validacion")
def validacion(_: UsuarioActual, lote_id: str, numero: int) -> dict:
    version = next((v for v in lotes.detalle(lote_id)["versiones"] if v["numero"] == numero), None)
    if version is None:
        raise lotes.ErrorDeLote(404, "NO_EXISTE", f"El lote no tiene versión v{numero}.")
    return version["validacion"]


@router.get("/lotes/{lote_id}/diff")
def diff(_: UsuarioActual, lote_id: str, desde: int, hasta: int) -> dict:
    return lotes.diff(lote_id, desde, hasta)


# --- Partidas y adjuntos ---------------------------------------------------


@router.patch("/lotes/{lote_id}/partidas/{clave}")
def actualizar_partida(usuario: UsuarioActual, lote_id: str, clave: str, datos: CambioPartida) -> dict:
    return lotes.actualizar_partida(usuario, lote_id, clave, datos.version, datos.model_dump(exclude={"version"}, exclude_unset=True))


@router.post("/lotes/{lote_id}/adjuntos", status_code=201)
async def subir_adjunto(usuario: UsuarioActual, lote_id: str, archivo: Annotated[UploadFile, File()],
                        clave_partida: Annotated[str | None, Form()] = None) -> dict:
    return lotes.subir_adjunto(usuario, lote_id, await archivo.read(), archivo.filename or "respaldo", clave_partida)


@router.get("/lotes/{lote_id}/adjuntos/{adjunto_id}")
def bajar_adjunto(usuario: UsuarioActual, lote_id: str, adjunto_id: str) -> FileResponse:
    ruta, nombre, mime = lotes.archivo_adjunto(usuario, lote_id, adjunto_id)
    return FileResponse(ruta, media_type=mime, filename=nombre, content_disposition_type="attachment")


@router.delete("/lotes/{lote_id}/adjuntos/{adjunto_id}")
def retirar_adjunto(usuario: UsuarioActual, lote_id: str, adjunto_id: str) -> dict:
    return lotes.retirar_adjunto(usuario, lote_id, adjunto_id)


# --- Revisión --------------------------------------------------------------


@router.post("/lotes/{lote_id}/enviar")
async def enviar(usuario: UsuarioActual, lote_id: str, version: Annotated[int, Form()],
                 comentario: Annotated[str, Form()] = "", evidencia: Evidencia = []) -> dict:  # noqa: B006
    return lotes.enviar(usuario, lote_id, version, comentario, await _leer(evidencia))


@router.post("/lotes/{lote_id}/tomar")
def tomar(usuario: UsuarioActual, lote_id: str) -> dict:
    return lotes.tomar(usuario, lote_id)


@router.put("/lotes/{lote_id}/controles/{control}")
def control(usuario: UsuarioActual, lote_id: str, control: str, datos: MarcaControl) -> dict:
    return lotes.marcar_control(usuario, lote_id, control, datos.version, datos.ok)


@router.post("/lotes/{lote_id}/observaciones")
def observar(usuario: UsuarioActual, lote_id: str, datos: NuevaObservacion) -> dict:
    return lotes.observar(usuario, lote_id, datos.version, datos.texto, datos.origen, datos.clave_partida)


@router.patch("/lotes/{lote_id}/observaciones/{observacion_id}")
def resolver(usuario: UsuarioActual, lote_id: str, observacion_id: str, datos: Resolucion) -> dict:
    return lotes.resolver_observacion(usuario, lote_id, observacion_id, datos.resuelta)


@router.post("/lotes/{lote_id}/rechazar")
def rechazar(usuario: UsuarioActual, lote_id: str, datos: Rechazo) -> dict:
    return lotes.rechazar(usuario, lote_id, datos.version, datos.motivo, [o.model_dump() for o in datos.observaciones])


@router.post("/lotes/{lote_id}/aprobar")
async def aprobar(usuario: UsuarioActual, lote_id: str, version: Annotated[int, Form()], evidencia: Evidencia = []) -> dict:  # noqa: B006
    return lotes.aprobar(usuario, lote_id, version, await _leer(evidencia))


@router.post("/lotes/{lote_id}/anular")
def anular(usuario: UsuarioActual, lote_id: str, datos: Anulacion) -> dict:
    return lotes.anular(usuario, lote_id, datos.motivo)


# --- Exportación fiscal ----------------------------------------------------


@router.post("/lotes/{lote_id}/exportaciones", status_code=201)
def exportar(usuario: UsuarioActual, lote_id: str, datos: PedidoExportacion) -> dict:
    return lotes.exportar(usuario, lote_id, datos.tipo)


@router.get("/lotes/{lote_id}/exportaciones/{exportacion_id}")
def bajar_exportacion(usuario: UsuarioActual, lote_id: str, exportacion_id: str) -> FileResponse:
    ruta, nombre = lotes.archivo_exportacion(usuario, lote_id, exportacion_id)
    return FileResponse(ruta, media_type="text/plain; charset=utf-8", filename=nombre)


# --- Auditoría y notificaciones -------------------------------------------


@router.get("/lotes/{lote_id}/auditoria")
def auditoria_lote(_: UsuarioActual, lote_id: str) -> dict:
    return {"eventos": lotes.auditoria_de(lote_id)}


def _solo_admin(usuario: Usuario) -> None:
    if not usuario.tiene("supervisor"):
        raise lotes.ErrorDeLote(403, "SIN_PERMISO", "Solo un supervisor consulta la auditoría completa.")


@router.get("/auditoria")
def auditoria_general(usuario: UsuarioActual, desde: str | None = None, hasta: str | None = None,
                      usuario_id: str | None = None, accion: str | None = None) -> dict:
    _solo_admin(usuario)
    with transaccion(escritura=False) as c:
        return {"eventos": auditoria.listar(c, usuario=usuario_id, accion=accion, desde=desde, hasta=hasta)}


@router.get("/auditoria/verificar")
def verificar_auditoria(usuario: UsuarioActual) -> dict:
    _solo_admin(usuario)
    with transaccion(escritura=False) as c:
        return auditoria.verificar(c)


@router.get("/notificaciones")
def notificaciones(usuario: UsuarioActual) -> dict:
    return {"notificaciones": lotes.notificaciones(usuario)}


@router.post("/notificaciones/{notificacion_id}/leida", status_code=204)
def leida(usuario: UsuarioActual, notificacion_id: str) -> None:
    lotes.marcar_leida(usuario, notificacion_id)
