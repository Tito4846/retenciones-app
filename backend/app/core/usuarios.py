"""
Quién está usando el módulo.

No hay ingreso propio: la identidad y los roles los va a dar la página principal
(el portal desde donde se abre este módulo). El contrato es simple: cada pedido a
la API trae

    X-Usuario-Id:      identificador estable de la persona (no cambia si cambia el nombre)
    X-Usuario-Nombre:  cómo se muestra (URL-encoded si lleva acentos)
    X-Usuario-Roles:   "empleado", "supervisor" o los dos separados por coma

Sin esos encabezados se usa el usuario por defecto de la configuración
(USUARIO_POR_DEFECTO_*), para trabajar solo en una computadora.

Mientras la página principal no esté conectada, los encabezados los manda el
front con el selector de usuarios de prueba. OJO: cualquiera puede mandarlos.
Antes de exponer el módulo a internet, la página principal tiene que ponerlos
detrás de un proxy propio (que pise lo que mande el navegador) o firmarlos.

Dos roles: el empleado prepara los lotes; el supervisor además los revisa,
aprueba o devuelve, reasigna y consulta la auditoría. Nadie aprueba lo que
preparó (eso lo controla la segregación de funciones, no el rol).
"""

from dataclasses import dataclass, field
from urllib.parse import unquote

from fastapi import HTTPException, Request

from app.core.config import settings

ROLES = ("empleado", "supervisor")

ENCABEZADO_ID = "x-usuario-id"
ENCABEZADO_NOMBRE = "x-usuario-nombre"
ENCABEZADO_ROLES = "x-usuario-roles"


@dataclass(frozen=True)
class Usuario:
    id: str
    nombre: str
    roles: frozenset[str] = field(default_factory=frozenset)
    ip: str = ""
    user_agent: str = ""

    def tiene(self, rol: str) -> bool:
        return rol in self.roles


def ip_de(request: Request) -> str:
    # Sin proxy delante: la IP de la conexión. (X-Forwarded-For lo puede inventar el cliente.)
    return request.client.host if request.client else ""


def leer_roles(texto: str) -> frozenset[str]:
    """'supervisor, empleado' -> {'supervisor', 'empleado'}. Lo que no es un rol se ignora."""
    return frozenset(r.strip().lower() for r in texto.split(",") if r.strip().lower() in ROLES)


def _sin_permiso(mensaje: str) -> HTTPException:
    return HTTPException(403, {"codigo": "SIN_ROL", "mensaje": mensaje, "errores": []})


def usuario_actual(request: Request) -> Usuario:
    """Dependencia de FastAPI: quién hace el pedido, según la página principal o el usuario por defecto."""
    usuario_id = unquote(request.headers.get(ENCABEZADO_ID, "")).strip()[:100]
    if usuario_id:
        nombre = unquote(request.headers.get(ENCABEZADO_NOMBRE, "")).strip()[:120] or usuario_id
        roles = leer_roles(unquote(request.headers.get(ENCABEZADO_ROLES, "")))
    else:
        usuario_id = settings.USUARIO_POR_DEFECTO_ID
        nombre = settings.USUARIO_POR_DEFECTO_NOMBRE
        roles = leer_roles(settings.USUARIO_POR_DEFECTO_ROLES)
    if not roles:
        raise _sin_permiso(f"{nombre} no tiene un rol para usar este módulo (empleado o supervisor).")
    return Usuario(usuario_id, nombre, roles, ip_de(request), request.headers.get("user-agent", "")[:200])
