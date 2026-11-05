"""
Piezas criptográficas del login. Sin base de datos: eso está en services/cuentas.py.

- Contraseñas: Argon2id (parámetros por defecto de argon2-cffi, los que recomienda
  OWASP), con rehash automático si cambian los parámetros.
- Token de acceso: JWT HS256 de vida corta (ACCESO_MIN), va en memoria del front y
  en el encabezado Authorization. Nunca en localStorage ni en una cookie.
- Token de renovación: 256 bits al azar, en cookie HttpOnly + SameSite=Strict. En
  la base se guarda solo su SHA-256: si alguien copia la base no puede usarlo.
- Segundo factor: TOTP (RFC 6238, 30 s, 6 dígitos), el de Google Authenticator.
  El secreto se guarda cifrado (Fernet) con una clave derivada de SECRET_KEY.
- Límite de intentos por IP, en memoria (además del bloqueo por cuenta, que está
  en la base).
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import stat
import struct
import threading
import time
import unicodedata
from collections import deque
from pathlib import Path
from urllib.parse import quote

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

EMISOR = "retenciones-app"
AUDIENCIA = "retenciones-api"
_hasher = PasswordHasher()
# Se usa cuando el correo no existe, para que la respuesta tarde lo mismo
# (si no, por el tiempo se podría saber qué correos tienen cuenta).
_HASH_FALSO = _hasher.hash(secrets.token_urlsafe(16))


# ----------------------------------------------------------------------
# Clave del servidor
# ----------------------------------------------------------------------

_clave_cache: dict[Path, bytes] = {}


def clave_servidor() -> bytes:
    """
    SECRET_KEY del .env o, si no hay, una generada la primera vez y guardada en
    DIRECTORIO_DATOS/.secreto (fuera de git). Cambiarla cierra todas las sesiones
    y deja inservibles los segundos factores registrados.
    """
    if settings.SECRET_KEY:
        return settings.SECRET_KEY.encode("utf-8")
    ruta = Path(settings.DIRECTORIO_DATOS) / ".secreto"
    if ruta not in _clave_cache:
        if not ruta.exists():
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(secrets.token_urlsafe(64), encoding="ascii")
            os.chmod(ruta, stat.S_IREAD | stat.S_IWRITE)
        _clave_cache[ruta] = ruta.read_text(encoding="ascii").strip().encode("ascii")
    return _clave_cache[ruta]


def _derivada(proposito: str) -> bytes:
    return hmac.new(clave_servidor(), proposito.encode("utf-8"), hashlib.sha256).digest()


# ----------------------------------------------------------------------
# Contraseñas
# ----------------------------------------------------------------------

CLAVE_MIN = 10
CLAVE_MAX = 128
# Las más usadas: no tiene sentido aceptarlas aunque cumplan el largo.
_COMUNES = {
    "123456789012", "1234567890123", "contraseña123", "contrasena123", "password1234", "password123456",
    "qwertyuiop12", "qwerty123456", "administrador", "administrador1", "retenciones1", "retenciones123",
    "000000000000", "111111111111", "abcdefghijkl", "abc123abc123", "iloveyou1234", "argentina123",
    "argentina2026", "bocajuniors12", "riverplate12", "contraseña1234", "micontraseña", "12345678abcd",
}


def politica_clave(clave: str, email: str = "", nombre: str = "") -> str | None:
    """None si sirve; si no, el motivo para mostrar. Sin reglas de mayúsculas/símbolos (NIST 800-63B)."""
    normalizada = unicodedata.normalize("NFKC", clave)
    if len(normalizada) < CLAVE_MIN:
        return f"La contraseña tiene que tener al menos {CLAVE_MIN} caracteres. Una frase larga es más fácil de recordar."
    if len(normalizada) > CLAVE_MAX:
        return f"La contraseña no puede pasar de {CLAVE_MAX} caracteres."
    minuscula = normalizada.lower()
    if minuscula in _COMUNES or len(set(minuscula)) < 4:
        return "Esa contraseña es demasiado común o repetitiva."
    plana = _sin_acentos(minuscula)
    # Partes del correo y del nombre de 4 letras o más: «gomez» en «anagomez2026» no va.
    partes = re.split(r"[^a-z0-9]+", _sin_acentos(email.split("@")[0].lower() + " " + nombre.lower()))
    if any(len(p) >= 4 and p in plana for p in partes):
        return "La contraseña no puede contener tu correo ni tu nombre."
    return None


def _sin_acentos(texto: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(ch))


def hashear_clave(clave: str) -> str:
    return _hasher.hash(unicodedata.normalize("NFKC", clave))


def verificar_clave(hash_guardado: str | None, clave: str) -> bool:
    """Siempre hace el mismo trabajo, exista o no el usuario."""
    try:
        return _hasher.verify(hash_guardado or _HASH_FALSO, unicodedata.normalize("NFKC", clave)) and bool(hash_guardado)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def necesita_rehash(hash_guardado: str) -> bool:
    return _hasher.check_needs_rehash(hash_guardado)


def clave_temporal() -> str:
    """Para altas y reseteos: 4 grupos de 4, sin caracteres que se confundan (0/O, 1/l)."""
    alfabeto = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(alfabeto) for _ in range(4)) for _ in range(4))


# ----------------------------------------------------------------------
# Tokens opacos (renovación, desafíos, configuración inicial)
# ----------------------------------------------------------------------


def token_opaco() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# Token de acceso (JWT)
# ----------------------------------------------------------------------


def emitir_acceso(usuario_id: str, version_token: int, sesion: str) -> tuple[str, int]:
    ahora = int(time.time())
    expira = ahora + settings.ACCESO_MIN * 60
    cuerpo = {
        "iss": EMISOR, "aud": AUDIENCIA, "sub": usuario_id, "ver": version_token, "sid": sesion,
        "iat": ahora, "nbf": ahora, "exp": expira, "jti": secrets.token_hex(8),
    }
    return jwt.encode(cuerpo, _derivada("jwt-acceso"), algorithm="HS256"), expira


def leer_acceso(token: str) -> dict | None:
    """El contenido si la firma, el emisor, la audiencia y el vencimiento están bien; si no, None."""
    try:
        return jwt.decode(
            token, _derivada("jwt-acceso"), algorithms=["HS256"], audience=AUDIENCIA, issuer=EMISOR,
            options={"require": ["exp", "iat", "sub", "ver", "sid"]}, leeway=5,
        )
    except jwt.PyJWTError:
        return None


# ----------------------------------------------------------------------
# Segundo factor (TOTP)
# ----------------------------------------------------------------------

PASO_TOTP = 30


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derivada("totp-secretos")))


def nuevo_secreto_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def descifrar(cifrado: str) -> str | None:
    try:
        return _fernet().decrypt(cifrado.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def _codigo(secreto: str, paso: int) -> str:
    clave = base64.b32decode(secreto, casefold=True)
    digest = hmac.new(clave, struct.pack(">Q", paso), hashlib.sha1).digest()
    corte = digest[-1] & 0x0F
    numero = struct.unpack(">I", digest[corte:corte + 4])[0] & 0x7FFFFFFF
    return f"{numero % 1_000_000:06d}"


def verificar_totp(secreto: str, codigo: str, ultimo_paso: int | None) -> int | None:
    """
    El paso de tiempo que validó (para guardarlo y no aceptar el mismo código dos
    veces), o None. Acepta un paso antes y uno después por diferencias de reloj.
    """
    codigo = "".join(ch for ch in codigo if ch.isdigit())
    if len(codigo) != 6:
        return None
    actual = int(time.time()) // PASO_TOTP
    for paso in (actual - 1, actual, actual + 1):
        if ultimo_paso is not None and paso <= ultimo_paso:
            continue
        if hmac.compare_digest(_codigo(secreto, paso), codigo):
            return paso
    return None


def uri_totp(secreto: str, email: str) -> str:
    emisor = "Retenciones ARCA"
    return f"otpauth://totp/{quote(emisor)}:{quote(email)}?secret={secreto}&issuer={quote(emisor)}&algorithm=SHA1&digits=6&period=30"


def qr_png(texto: str) -> str:
    """El QR como data URI PNG (el front lo muestra en un <img> sin tocar el sanitizador)."""
    import segno

    return segno.make(texto, error="m").png_data_uri(scale=6, border=2, dark="#111827", light="#ffffff")


def codigos_recuperacion(cantidad: int = 10) -> list[str]:
    alfabeto = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alfabeto) for _ in range(5)) for _ in range(2)) for _ in range(cantidad)]


def hash_recuperacion(codigo: str) -> str:
    limpio = codigo.strip().lower().replace(" ", "")
    return hmac.new(_derivada("recuperacion"), limpio.encode("utf-8"), hashlib.sha256).hexdigest()


# ----------------------------------------------------------------------
# Límite de intentos por IP (en memoria; se reinicia con el servidor)
# ----------------------------------------------------------------------


class Limitador:
    def __init__(self, maximo: int, ventana_s: int):
        self.maximo = maximo
        self.ventana = ventana_s
        self._marcas: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> bool:
        ahora = time.monotonic()
        with self._lock:
            marcas = self._marcas.setdefault(clave, deque())
            while marcas and ahora - marcas[0] > self.ventana:
                marcas.popleft()
            if len(marcas) >= self.maximo:
                return False
            marcas.append(ahora)
            return True

    def limpiar(self) -> None:
        with self._lock:
            self._marcas.clear()


# 20 intentos de ingreso (contraseña o código) cada 5 minutos por IP.
limitador_ingreso = Limitador(20, 300)
