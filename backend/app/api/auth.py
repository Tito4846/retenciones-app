"""
Ingreso, sesión y gestión de usuarios. La lógica está en services/cuentas.py.

El token de renovación viaja solo en una cookie HttpOnly (el JavaScript del front
no lo puede leer), SameSite=Strict y limitada a /api/auth. Los dos pedidos que la
usan (`/renovar` y `/salir`) además exigen el encabezado X-Requested-With y un
Origin permitido: un sitio ajeno no puede dispararlos (CSRF).
"""

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.usuarios import Usuario, anonimo, usuario_actual
from app.services import cuentas

router = APIRouter(tags=["ingreso"])
UsuarioActual = Annotated[Usuario, Depends(usuario_actual)]

COOKIE = "retenciones_renovacion"
RUTA_COOKIE = "/api/auth"
_ORIGEN_LOCAL = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


def _origen_permitido(request: Request) -> None:
    """Defensa CSRF para los pedidos que usan la cookie."""
    if request.headers.get("x-requested-with") != "retenciones":
        raise HTTPException(403, {"codigo": "CSRF", "mensaje": "Pedido no permitido.", "errores": []})
    origen = request.headers.get("origin")
    # El front servido por el mismo backend (Render): el origen es el propio host.
    mismo_sitio = bool(origen) and urlsplit(origen).netloc == request.headers.get("host")
    if origen and not (mismo_sitio or _ORIGEN_LOCAL.match(origen) or origen in settings.CORS_ORIGINS):
        raise HTTPException(403, {"codigo": "CSRF", "mensaje": "Origen no permitido.", "errores": []})


def _con_sesion(respuesta: Response, resultado: dict) -> dict:
    """Pasa el token de renovación a la cookie y lo saca del cuerpo."""
    renovacion = resultado.pop("_renovacion", None)
    resultado.pop("_renovacion_vence", None)
    if renovacion:
        respuesta.set_cookie(
            COOKIE, renovacion, max_age=settings.SESION_HORAS * 3600, path=RUTA_COOKIE,
            httponly=True, secure=settings.COOKIE_SECURE, samesite="strict",
        )
    return resultado


def _borrar_cookie(respuesta: Response) -> None:
    respuesta.delete_cookie(COOKIE, path=RUTA_COOKIE, httponly=True, secure=settings.COOKIE_SECURE, samesite="strict")


# Límites de largo: nadie manda una contraseña de 10 MB para trabar el servidor con Argon2.
Clave = Annotated[str, Field(min_length=1, max_length=256)]
Token = Annotated[str, Field(min_length=10, max_length=200)]
Codigo = Annotated[str, Field(min_length=6, max_length=20)]


class Ingreso(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=254)]
    clave: Clave


class Configuracion(BaseModel):
    token: Token
    clave: Clave


class ConDesafio(BaseModel):
    desafio: Token


class CodigoDesafio(ConDesafio):
    codigo: Codigo


class ClaveDesafio(ConDesafio):
    nueva: Clave


class CambioClave(BaseModel):
    actual: Clave
    nueva: Clave


class SoloClave(BaseModel):
    clave: Clave


class NuevoUsuario(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=254)]
    nombre: Annotated[str, Field(min_length=1, max_length=80)]
    rol: Literal["empleado", "supervisor"]


class CambiosUsuario(BaseModel):
    nombre: Annotated[str, Field(max_length=80)] | None = None
    rol: Literal["empleado", "supervisor"] | None = None
    activo: bool | None = None
    gestiona_usuarios: bool | None = None


# ----------------------------------------------------------------------
# Ingreso
# ----------------------------------------------------------------------


@router.get("/auth/estado")
def estado() -> dict:
    """Si la cuenta principal todavía no eligió contraseña (el front muestra cómo seguir)."""
    return {"configurada": not cuentas.necesita_configuracion(), "cuenta_principal": settings.CUENTA_PRINCIPAL.strip().lower()}


@router.post("/auth/configurar", status_code=204)
def configurar(datos: Configuracion, request: Request) -> None:
    cuentas.configurar_principal(datos.token, datos.clave, anonimo(request))


@router.post("/auth/ingresar")
def ingresar(datos: Ingreso, request: Request, respuesta: Response) -> dict:
    return _con_sesion(respuesta, cuentas.ingresar(datos.email, datos.clave, anonimo(request, datos.email.strip().lower())))


@router.post("/auth/2fa")
def segundo_factor(datos: CodigoDesafio, request: Request, respuesta: Response) -> dict:
    return _con_sesion(respuesta, cuentas.verificar_2fa(datos.desafio, datos.codigo, anonimo(request)))


@router.post("/auth/cambiar-clave-inicial")
def cambiar_clave_inicial(datos: ClaveDesafio, request: Request, respuesta: Response) -> dict:
    return _con_sesion(respuesta, cuentas.cambiar_clave_inicial(datos.desafio, datos.nueva, anonimo(request)))


@router.post("/auth/2fa/enrolar")
def enrolar(datos: ConDesafio) -> dict:
    return cuentas.iniciar_enrolamiento(datos.desafio)


@router.post("/auth/2fa/confirmar")
def confirmar(datos: CodigoDesafio, request: Request, respuesta: Response) -> dict:
    return _con_sesion(respuesta, cuentas.confirmar_enrolamiento(datos.desafio, datos.codigo, anonimo(request)))


@router.post("/auth/renovar", response_model=None)
def renovar(request: Request, respuesta: Response) -> dict | JSONResponse:
    _origen_permitido(request)
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, {"codigo": "SIN_SESION", "mensaje": "Ingresá con tu correo y contraseña.", "errores": []})
    try:
        return _con_sesion(respuesta, cuentas.renovar(token, anonimo(request)))
    except cuentas.ErrorDeLote as error:
        if error.estado != 401:
            raise
        # Sesión terminada: además del 401, que el navegador tire la cookie.
        fallo = JSONResponse({"detail": error.detalle()}, status_code=401, headers={"Cache-Control": "no-store"})
        _borrar_cookie(fallo)
        return fallo


@router.post("/auth/salir", status_code=204)
def salir(request: Request, respuesta: Response) -> None:
    _origen_permitido(request)
    quien = None
    encabezado = request.headers.get("authorization", "")
    if encabezado.lower().startswith("bearer "):
        try:
            quien = usuario_actual(request)
        except HTTPException:
            quien = None
    cuentas.salir(request.cookies.get(COOKIE), quien or anonimo(request))
    _borrar_cookie(respuesta)


# ----------------------------------------------------------------------
# Mi cuenta
# ----------------------------------------------------------------------


@router.get("/auth/yo")
def yo(usuario: UsuarioActual) -> dict:
    return cuentas.yo(usuario)


@router.post("/auth/cambiar-clave")
def cambiar_clave(datos: CambioClave, usuario: UsuarioActual, respuesta: Response) -> dict:
    return _con_sesion(respuesta, cuentas.cambiar_clave(usuario, datos.actual, datos.nueva))


@router.post("/auth/mi-2fa")
def iniciar_mi_2fa(usuario: UsuarioActual) -> dict:
    return cuentas.iniciar_mi_2fa(usuario)


@router.post("/auth/mi-2fa/confirmar")
def confirmar_mi_2fa(datos: CodigoDesafio, usuario: UsuarioActual) -> dict:
    return cuentas.confirmar_enrolamiento(datos.desafio, datos.codigo, usuario, solo_de=usuario.id)


@router.post("/auth/mi-2fa/desactivar", status_code=204)
def desactivar_mi_2fa(datos: SoloClave, usuario: UsuarioActual) -> None:
    cuentas.desactivar_mi_2fa(usuario, datos.clave)


# ----------------------------------------------------------------------
# Usuarios
# ----------------------------------------------------------------------


@router.get("/usuarios")
def listar(usuario: UsuarioActual) -> dict:
    return {"usuarios": cuentas.listar_usuarios(usuario)}


@router.post("/usuarios", status_code=201)
def crear(datos: NuevoUsuario, usuario: UsuarioActual) -> dict:
    return cuentas.crear_usuario(usuario, datos.email, datos.nombre, datos.rol)


@router.patch("/usuarios/{usuario_id}")
def actualizar(usuario_id: str, datos: CambiosUsuario, usuario: UsuarioActual) -> dict:
    return cuentas.actualizar_usuario(usuario, usuario_id, datos.nombre, datos.rol, datos.activo, datos.gestiona_usuarios)


@router.post("/usuarios/{usuario_id}/resetear-clave")
def resetear_clave(usuario_id: str, usuario: UsuarioActual) -> dict:
    return cuentas.resetear_clave(usuario, usuario_id)


@router.post("/usuarios/{usuario_id}/resetear-2fa")
def resetear_2fa(usuario_id: str, usuario: UsuarioActual) -> dict:
    return cuentas.resetear_2fa(usuario, usuario_id)


@router.post("/usuarios/{usuario_id}/desbloquear")
def desbloquear(usuario_id: str, usuario: UsuarioActual) -> dict:
    return cuentas.desbloquear(usuario, usuario_id)
