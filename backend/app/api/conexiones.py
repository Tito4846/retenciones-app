"""
Rutas de "Conexiones": conectar y desconectar los bots de ARCA y Xubio, y
conciliar con lo que trajeron.

Las credenciales llegan en el cuerpo del POST, se usan para entrar y no se
guardan en disco ni se devuelven nunca (el estado solo trae la CUIT o el mail).
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.bots import conexiones, misret
from app.bots.navegador_arca import ArcaDesconectado, ErrorArca
from app.bots.navegador_xubio import XubioDesconectado
from app.services import tareas

router = APIRouter(prefix="/conexiones")


class CuentaArca(BaseModel):
    cuit: str = Field(min_length=11, max_length=13)
    clave: str = Field(min_length=1)


class CuentaXubio(BaseModel):
    # Vacios = la cuenta del .env, o la persona entra a mano en la ventana.
    usuario: str | None = None
    clave: str | None = None


class Periodo(BaseModel):
    # Vacios = el rango de las retenciones de ARCA mas la tolerancia.
    desde: date | None = None
    hasta: date | None = None


@router.get("")
def estado() -> dict:
    return conexiones.estado()


@router.post("/arca")
def conectar_arca(cuenta: CuentaArca) -> dict:
    try:
        conexiones.arca.conectar(cuenta.cuit, cuenta.clave)
    except ErrorArca as e:
        raise HTTPException(400, str(e)) from e
    return conexiones.estado()


@router.delete("/arca")
def desconectar_arca() -> dict:
    conexiones.arca.desconectar()
    return conexiones.estado()


@router.post("/arca/mostrar", status_code=204)
def mostrar_arca() -> None:
    """Trae la ventana de ARCA al frente para exportar las retenciones."""
    try:
        conexiones.arca.mostrar()
    except ArcaDesconectado as e:
        raise HTTPException(409, str(e)) from e


CodigoImpuesto = Annotated[str, Field(pattern=r"^\d{1,4}$")]


class ConsultaMisRetenciones(BaseModel):
    """Lo mismo que pide el formulario de Mis Retenciones (versión anterior)."""

    cuit_retenido: str = Field(min_length=11, max_length=11)
    cuit_agente: str | None = Field(default=None, max_length=11)
    # ARCA consulta uno por vez: el bot repite el formulario con cada uno.
    # Vacia = los que tienen cuenta en el Libro Mayor (flujo automatico) o, si
    # no, Ganancias e IVA.
    impuestos: list[CodigoImpuesto] = Field(default_factory=list, max_length=20)
    tipo: Literal["Retencion", "Percepcion", "RetencionPercepcion"] = "RetencionPercepcion"
    desde: date
    hasta: date

    @model_validator(mode="after")
    def _fechas_en_orden(self) -> "ConsultaMisRetenciones":
        if self.desde > self.hasta:
            raise ValueError("La fecha desde es posterior a la fecha hasta.")
        return self


@router.post("/arca/consultar", status_code=202)
def consultar_arca(consulta: ConsultaMisRetenciones) -> dict:
    """El bot completa el formulario de ARCA; el avance se sigue en el estado."""
    datos = consulta.model_dump()
    if not datos["impuestos"]:
        datos["impuestos"] = misret.impuestos_a_consultar((conexiones.arca.formulario() or {}).get("impuestos", []))
    try:
        conexiones.arca.consultar(misret.ConsultaArca(**datos))
    except ArcaDesconectado as e:
        raise HTTPException(409, str(e)) from e
    except misret.ErrorConsulta as e:
        raise HTTPException(400, str(e)) from e
    return conexiones.estado()


@router.post("/arca/cancelar")
def cancelar_consulta_arca() -> dict:
    """Corta «Solo consultar ARCA»: el bot termina el impuesto en curso y no sigue."""
    conexiones.arca.cancelar()
    return conexiones.estado()


@router.post("/arca/captura")
def capturar_arca() -> dict:
    """Guarda el HTML de la ventana de ARCA en datos/debug (para ajustar el bot)."""
    try:
        return {"archivos": conexiones.arca.capturar()}
    except ArcaDesconectado as e:
        raise HTTPException(409, str(e)) from e


@router.post("/xubio")
def conectar_xubio(cuenta: CuentaXubio) -> dict:
    conexiones.xubio.conectar(cuenta.usuario, cuenta.clave)
    return conexiones.estado()


@router.delete("/xubio")
def desconectar_xubio() -> dict:
    conexiones.xubio.desconectar()
    return conexiones.estado()


@router.post("/xubio/mostrar", status_code=204)
def mostrar_xubio() -> None:
    try:
        conexiones.xubio.mostrar()
    except XubioDesconectado as e:
        raise HTTPException(409, str(e)) from e


@router.post("/conciliar", status_code=202)
def conciliar(background: BackgroundTasks, periodo: Periodo | None = None) -> dict:
    """Arranca el cruce con la descarga de ARCA y el mayor de Xubio (mismo polling que la subida)."""
    if conexiones.arca.ultima_descarga() is None:
        raise HTTPException(409, "Todavía no hay retenciones de ARCA: exportalas desde la ventana del bot.")
    if not conexiones.xubio.conectado:
        raise HTTPException(409, "Xubio no está conectado.")

    periodo = periodo or Periodo()
    task_id = tareas.crear()
    background.add_task(
        tareas.procesar_con,
        task_id,
        lambda: conexiones.cargar_desde_conexiones(periodo.desde, periodo.hasta),
        identidad=lambda: (conexiones.xubio.estado()["empresa_activa"] or "", ""),
    )
    return {"task_id": task_id}


@router.post("/automatico", status_code=202)
def automatico(background: BackgroundTasks, consulta: ConsultaMisRetenciones) -> dict:
    """
    Todo en un paso: el Libro Mayor de Xubio, las retenciones de ARCA del mismo
    período y el cruce. El avance (paso y detalle) se sigue en /estado/{task_id}.
    """
    arca = conexiones.arca.estado()
    if arca["estado"] != "conectado":
        raise HTTPException(409, "ARCA no está conectado: conectalo en Conexiones.")
    if not arca["formulario"]:
        raise HTTPException(409, "La ventana de ARCA no está en el formulario de Mis Retenciones.")
    if arca["consultando"]:
        raise HTTPException(409, "Ya hay una consulta de ARCA en curso.")
    if not conexiones.xubio.conectado:
        raise HTTPException(409, "Xubio no está conectado: conectalo en Conexiones.")

    task_id = tareas.crear()
    background.add_task(
        tareas.procesar_con,
        task_id,
        lambda: conexiones.traer_automatico(
            **consulta.model_dump(),
            avance=lambda paso, detalle: tareas.avanzar(task_id, paso, detalle),
            cancelada=lambda: tareas.cancelada(task_id),
        ),
        hasta_periodo=consulta.hasta,
        identidad=lambda: (conexiones.xubio.estado()["empresa_activa"] or "", consulta.cuit_retenido),
    )
    return {"task_id": task_id}
