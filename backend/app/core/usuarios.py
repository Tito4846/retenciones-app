"""
Quién está usando el módulo.

Cada pedido a la API trae `Authorization: Bearer <token de acceso>` (lo emite
/api/auth/ingresar, ver services/cuentas.py). `usuario_actual` valida la firma y
el vencimiento, y además busca la cuenta en la base en cada pedido: si la
desactivaron, le cambiaron la contraseña o cerró la sesión, el token deja de
servir en el acto aunque no haya vencido.

Dos roles: el empleado prepara los lotes; el supervisor además los revisa,
aprueba o devuelve, reasigna y consulta la auditoría. Nadie aprueba lo que
preparó (eso lo controla la segregación de funciones, no el rol). La cuenta
principal y las que ella habilite gestionan los usuarios.
"""

from dataclasses import dataclass, field

from fastapi import HTTPException, Request

ROLES = ("empleado", "supervisor")


@dataclass(frozen=True)
class Usuario:
    id: str
    nombre: str
    roles: frozenset[str] = field(default_factory=frozenset)
    ip: str = ""
    user_agent: str = ""
    email: str = ""
    principal: bool = False
    gestiona_usuarios: bool = False
    sesion: str = ""

    def tiene(self, rol: str) -> bool:
        return rol in self.roles

    @property
    def puede_gestionar(self) -> bool:
        return self.principal or self.gestiona_usuarios


def ip_de(request: Request) -> str:
    # Sin proxy delante: la IP de la conexión. (X-Forwarded-For lo puede inventar el cliente.)
    return request.client.host if request.client else ""


def anonimo(request: Request, email: str = "") -> Usuario:
    """Para auditar intentos de quien todavía no entró."""
    return Usuario("", email or "(sin identificar)", frozenset(), ip_de(request), request.headers.get("user-agent", "")[:200], email)


def _no_autorizado(mensaje: str = "Tu sesión venció. Volvé a ingresar.") -> HTTPException:
    return HTTPException(401, {"codigo": "SIN_SESION", "mensaje": mensaje, "errores": []},
                         headers={"WWW-Authenticate": 'Bearer realm="retenciones"'})


def usuario_actual(request: Request) -> Usuario:
    """Dependencia de FastAPI: el usuario del token de acceso, o 401."""
    from app.services import cuentas

    encabezado = request.headers.get("authorization", "")
    tipo, _, token = encabezado.partition(" ")
    if tipo.lower() != "bearer" or not token:
        raise _no_autorizado("Ingresá con tu correo y contraseña.")
    usuario = cuentas.usuario_del_token(token.strip(), ip_de(request), request.headers.get("user-agent", "")[:200])
    if usuario is None:
        raise _no_autorizado()
    return usuario
