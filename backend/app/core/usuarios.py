"""
Quién está usando el módulo, con los niveles de acceso de ContadorWeb.

No hay ingreso propio: la identidad la da ContadorWeb (la página principal),
igual que con el módulo de Planes de pago. ContadorWeb firma un «pase» con el
usuario, su nivel y sus módulos, y abre este módulo en

    https://<este módulo>/?pase=<pase>

El front lo canjea en POST /api/sesion/entrar por un token de sesión propio
(12 h) y lo manda en `Authorization: Bearer` en cada pedido.

Formato del pase (el mismo `firmar_pase` de ContadorWeb, api/rutas/planes.py):
    base64url(JSON) + "." + base64url(HMAC-SHA256(RETENCIONES_MODULO_SECRETO, ese texto))
con el JSON
    {"u": "carlos", "n": "Piccinini Carlos", "nivel": 200, "nivel_nombre": "...",
     "config": true, "modulos": ["*"], "general": true, "estudio": "piccinini",
     "estudio_nombre": "...", "exp": <epoch>, "id": "<al azar>"}
(lo que devuelve accesos.permisos() de ContadorWeb, más u/n/exp/id). Es de un
solo uso y vence a los pocos minutos.

Niveles de ContadorWeb -> roles de este módulo:
    200 Administrador General, 100 Administrador, o un nivel con Configuración
    o con todos los módulos ("*")                          -> supervisor
    un nivel con el módulo «Impuestos/Retenciones - Supervisión»  -> supervisor
    un nivel con el módulo «Impuestos/Retenciones»          -> empleado
    cualquier otro                                         -> sin acceso (403)

El empleado prepara los lotes; el supervisor además los revisa, aprueba o
devuelve, reasigna y consulta la auditoría. Nadie aprueba lo que preparó (eso
lo controla la segregación de funciones, no el rol).

Modo local (sin RETENCIONES_MODULO_SECRETO): para trabajar en una computadora sin
ContadorWeb. Cada pedido puede traer X-Usuario-Id / -Nombre / -Nivel / -Modulos
(lo hace el selector de usuarios de prueba del front); sin ellos, el usuario por
defecto del .env. OJO: en este modo cualquiera puede mandarlos. Con el secreto
configurado esos encabezados se ignoran y solo vale el token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import unquote

from fastapi import HTTPException, Request

from app.core.config import settings

ROLES = ("empleado", "supervisor")

# Los mismos códigos que accesos.py de ContadorWeb.
NIVEL_GENERAL = "200"
NIVEL_ADMIN = "100"
NIVEL_USUARIO = "10"
NIVELES_ADMINISTRADOR = (NIVEL_GENERAL, NIVEL_ADMIN)
NOMBRES_NIVEL = {NIVEL_GENERAL: "Administrador General", NIVEL_ADMIN: "Administrador", NIVEL_USUARIO: "Usuario"}
TODOS = "*"
# Claves «Menú/Opción» para tildar en ContadorWeb › Configuración › Niveles de acceso.
MODULO_RETENCIONES = "Impuestos/Retenciones"
MODULO_SUPERVISION = "Impuestos/Retenciones - Supervisión"

ENCABEZADO_ID = "x-usuario-id"
ENCABEZADO_NOMBRE = "x-usuario-nombre"
ENCABEZADO_NIVEL = "x-usuario-nivel"
ENCABEZADO_MODULOS = "x-usuario-modulos"

# ContadorWeb firma pases de 2 minutos; más de 10 no se acepta.
VIDA_MAXIMA_PASE_S = 600


@dataclass(frozen=True)
class Usuario:
    id: str
    nombre: str
    roles: frozenset[str] = field(default_factory=frozenset)
    ip: str = ""
    user_agent: str = ""
    nivel: str = ""
    nivel_nombre: str = ""
    estudio: str = ""
    estudio_nombre: str = ""

    def tiene(self, rol: str) -> bool:
        return rol in self.roles


def ip_de(request: Request) -> str:
    # Sin proxy delante: la IP de la conexión. (X-Forwarded-For lo puede inventar el cliente.)
    return request.client.host if request.client else ""


# --- Niveles de ContadorWeb -> roles --------------------------------------------


def roles_de(nivel: str | int, modulos: list[str] | tuple[str, ...] = (), config: bool = False,
             general: bool = False) -> frozenset[str]:
    """Los roles de este módulo según el nivel de acceso de ContadorWeb."""
    nivel = str(nivel).strip()
    modulos = {str(m).strip() for m in modulos}
    if general or config or nivel in NIVELES_ADMINISTRADOR or TODOS in modulos or MODULO_SUPERVISION in modulos:
        return frozenset({"supervisor"})
    if MODULO_RETENCIONES in modulos:
        return frozenset({"empleado"})
    return frozenset()


def _sin_permiso(nombre: str) -> HTTPException:
    return HTTPException(403, {
        "codigo": "SIN_ROL",
        "mensaje": f"El nivel de acceso de {nombre} en ContadorWeb no incluye Impuestos › Retenciones. "
                   "Pedíselo a un administrador.",
        "errores": [],
    })


def _sin_sesion(mensaje: str = "Tu sesión venció. Volvé a abrir Retenciones desde ContadorWeb.") -> HTTPException:
    return HTTPException(401, {"codigo": "SIN_SESION", "mensaje": mensaje, "errores": []},
                         headers={"WWW-Authenticate": 'Bearer realm="retenciones"'})


# --- Firmas ----------------------------------------------------------------------


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def secreto() -> bytes | None:
    """El secreto compartido con ContadorWeb. Sin él (o muy corto), modo local."""
    valor = (settings.RETENCIONES_MODULO_SECRETO or "").strip()
    return valor.encode("utf-8") if len(valor) >= 32 else None


def conectado() -> bool:
    return secreto() is not None


def firmar(datos: dict, clave: bytes) -> str:
    cuerpo = _b64(json.dumps(datos, separators=(",", ":"), ensure_ascii=False).encode())
    firma = _b64(hmac.new(clave, cuerpo.encode(), hashlib.sha256).digest())
    return f"{cuerpo}.{firma}"


def _leer_firmado(texto: str, clave: bytes) -> dict | None:
    texto = (texto or "").strip()
    if texto.count(".") != 1:
        return None
    cuerpo, firma = texto.split(".")
    esperada = _b64(hmac.new(clave, cuerpo.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        datos = json.loads(_unb64(cuerpo))
    except ValueError:
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("exp"), (int, float)) or datos["exp"] < time.time():
        return None
    return datos


def _clave_sesion(clave: bytes) -> bytes:
    # Otra clave para los tokens propios: un pase nunca sirve como token ni al revés.
    return hmac.new(clave, b"retenciones.sesion", hashlib.sha256).digest()


_pases_usados: dict[str, float] = {}
_lock = threading.Lock()


def _marcar_usado(pase_id: str, exp: float) -> bool:
    """False si el pase ya se usó (de un solo uso)."""
    ahora = time.time()
    with _lock:
        for k in [k for k, v in _pases_usados.items() if v < ahora]:
            del _pases_usados[k]
        if pase_id in _pases_usados:
            return False
        _pases_usados[pase_id] = exp
        return True


def canjear_pase(pase: str) -> tuple[str, int, dict]:
    """Valida el pase de ContadorWeb y emite el token de sesión: (token, vence, yo)."""
    clave = secreto()
    if clave is None:
        raise HTTPException(503, {"codigo": "SIN_CONTADORWEB", "errores": [],
                                  "mensaje": "El módulo no está conectado a ContadorWeb: falta RETENCIONES_MODULO_SECRETO."})
    datos = _leer_firmado(pase, clave)
    if not datos or not str(datos.get("u") or "").strip() or not str(datos.get("id") or ""):
        raise _sin_sesion("El enlace de ContadorWeb no es válido o venció. Volvé a abrir Retenciones desde el menú.")
    if datos["exp"] > time.time() + VIDA_MAXIMA_PASE_S or not _marcar_usado(str(datos["id"]), datos["exp"]):
        raise _sin_sesion("Ese enlace de ContadorWeb ya se usó. Volvé a abrir Retenciones desde el menú.")

    usuario_id = str(datos["u"]).strip().lower()[:100]
    nombre = str(datos.get("n") or usuario_id).strip()[:120]
    nivel = str(datos.get("nivel") or NIVEL_USUARIO)
    roles = roles_de(nivel, datos.get("modulos") or [], bool(datos.get("config")), bool(datos.get("general")))
    if not roles:
        raise _sin_permiso(nombre)

    vence = int(time.time() + settings.SESION_HORAS * 3600)
    sesion = {"u": usuario_id, "n": nombre, "r": sorted(roles), "nivel": nivel,
              "nn": str(datos.get("nivel_nombre") or NOMBRES_NIVEL.get(nivel, f"Nivel {nivel}"))[:40],
              "e": str(datos.get("estudio") or "")[:60], "en": str(datos.get("estudio_nombre") or "")[:120],
              "exp": vence, "s": secrets.token_urlsafe(8)}
    token = firmar(sesion, _clave_sesion(clave))
    return token, vence, como_yo(_de_sesion(sesion))


def _de_sesion(datos: dict, ip: str = "", user_agent: str = "") -> Usuario:
    roles = frozenset(r for r in datos.get("r") or [] if r in ROLES)
    return Usuario(datos["u"], datos.get("n") or datos["u"], roles, ip, user_agent, str(datos.get("nivel") or ""),
                   datos.get("nn") or "", datos.get("e") or "", datos.get("en") or "")


def como_yo(usuario: Usuario) -> dict:
    """Lo que ve el front en /api/yo."""
    return {"id": usuario.id, "nombre": usuario.nombre, "roles": sorted(usuario.roles), "nivel": usuario.nivel,
            "nivel_nombre": usuario.nivel_nombre, "estudio_nombre": usuario.estudio_nombre}


# --- Dependencia -----------------------------------------------------------------


def _modo_local(request: Request, ip: str, user_agent: str) -> Usuario:
    usuario_id = unquote(request.headers.get(ENCABEZADO_ID, "")).strip()[:100]
    if usuario_id:
        nombre = unquote(request.headers.get(ENCABEZADO_NOMBRE, "")).strip()[:120] or usuario_id
        nivel = unquote(request.headers.get(ENCABEZADO_NIVEL, "")).strip() or NIVEL_USUARIO
        modulos = unquote(request.headers.get(ENCABEZADO_MODULOS, ""))
    else:
        usuario_id = settings.USUARIO_POR_DEFECTO_ID
        nombre = settings.USUARIO_POR_DEFECTO_NOMBRE
        nivel = str(settings.USUARIO_POR_DEFECTO_NIVEL).strip()
        modulos = settings.USUARIO_POR_DEFECTO_MODULOS
    roles = roles_de(nivel, [m for m in modulos.split(",") if m.strip()])
    if not roles:
        raise _sin_permiso(nombre)
    return Usuario(usuario_id, nombre, roles, ip, user_agent, nivel, NOMBRES_NIVEL.get(nivel, f"Nivel {nivel}"))


def usuario_actual(request: Request) -> Usuario:
    """Dependencia de FastAPI: el usuario de la sesión de ContadorWeb (o el de modo local)."""
    ip = ip_de(request)
    user_agent = request.headers.get("user-agent", "")[:200]
    clave = secreto()
    if clave is None:
        return _modo_local(request, ip, user_agent)
    tipo, _, token = request.headers.get("authorization", "").partition(" ")
    if tipo.lower() != "bearer" or not token.strip():
        raise _sin_sesion("Entrá a Retenciones desde ContadorWeb (menú Impuestos › Retenciones).")
    datos = _leer_firmado(token, _clave_sesion(clave))
    if not datos or not datos.get("u"):
        raise _sin_sesion()
    usuario = _de_sesion(datos, ip, user_agent)
    if not usuario.roles:
        raise _sin_permiso(usuario.nombre)
    return usuario
