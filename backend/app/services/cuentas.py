"""
Cuentas, ingreso y sesiones.

Ingreso
-------
1. Correo + contraseña (`ingresar`). Error siempre genérico («Correo o contraseña
   incorrectos»), con el mismo trabajo de hash exista o no la cuenta: no se puede
   averiguar qué correos tienen cuenta. Cada fallo suma; a los INTENTOS_MAX la
   cuenta se bloquea BLOQUEO_MIN minutos. Además hay un límite por IP.
2. Si faltan pasos, en vez de la sesión vuelve un *desafío* (token de 10 minutos,
   5 intentos) con el próximo paso: `2fa` (código de la app o de recuperación),
   `cambiar_clave` (clave temporal) o `enrolar_2fa` (supervisor sin segundo factor,
   solo si DOSFA_SUPERVISORES).
3. Al terminar: token de acceso (JWT, ACCESO_MIN) en el cuerpo y token de
   renovación en cookie HttpOnly (lo pone la API).

Renovación
----------
El token de renovación se rota en cada uso. Si llega uno ya usado (alguien lo
copió), se revoca toda la sesión y queda en la auditoría como `token_reutilizado`.
Vence por inactividad (INACTIVIDAD_MIN) y, sí o sí, a las SESION_HORAS.

Revocación inmediata
--------------------
Cada pedido busca la cuenta: si está desactivada, si cambió su `version_token`
(cambio o reseteo de contraseña o de 2FA, cambio de rol) o si su sesión se cerró,
el token de acceso deja de servir aunque no haya vencido.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from app.core import seguridad as S
from app.core.config import settings
from app.core.db import transaccion
from app.core.usuarios import Usuario
from app.services import auditoria
from app.services.lotes import ErrorDeLote

log = logging.getLogger(__name__)

MENSAJE_CREDENCIALES = "Correo o contraseña incorrectos."
DESAFIO_MIN = 10
DESAFIO_INTENTOS = 5
# Dos pestañas que renuevan a la vez: la segunda no es un robo.
GRACIA_RENOVACION_S = 20
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
SISTEMA = Usuario("sistema", "Sistema", frozenset())


def _ahora() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(momento: datetime) -> str:
    return momento.isoformat().replace("+00:00", "Z")


def _vencido(iso: str | None) -> bool:
    return not iso or iso <= _iso(_ahora())


def _error(estado: int, codigo: str, mensaje: str) -> ErrorDeLote:
    return ErrorDeLote(estado, codigo, mensaje)


def _credenciales() -> ErrorDeLote:
    return _error(401, "CREDENCIALES", MENSAJE_CREDENCIALES)


def _normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def _usuario(fila, ip: str = "", user_agent: str = "", sesion: str = "") -> Usuario:
    return Usuario(
        fila["id"], fila["nombre"], frozenset({fila["rol"]}), ip, user_agent, fila["email"],
        bool(fila["principal"]), bool(fila["gestiona_usuarios"]), sesion,
    )


def publico(fila) -> dict:
    """Lo que se puede mostrar de una cuenta (nunca hashes ni secretos)."""
    return {
        "id": fila["id"], "email": fila["email"], "nombre": fila["nombre"], "rol": fila["rol"],
        "principal": bool(fila["principal"]), "gestiona_usuarios": bool(fila["gestiona_usuarios"]),
        "activo": bool(fila["activo"]), "tiene_2fa": bool(fila["totp_cifrado"]), "exige_2fa": _exige_2fa(fila),
        "debe_cambiar_clave": bool(fila["debe_cambiar_clave"]),
        "bloqueado": bool(fila["bloqueado_hasta"]) and not _vencido(fila["bloqueado_hasta"]),
        "codigos_recuperacion_restantes": len(json.loads(fila["recuperacion_json"] or "[]")),
        "ultimo_ingreso_utc": fila["ultimo_ingreso_utc"], "creado_utc": fila["creado_utc"],
    }


def _fila(c, usuario_id: str):
    return c.execute("SELECT * FROM usuario WHERE id = ?", (usuario_id,)).fetchone()


def _invalidar(c, usuario_id: str, borrar_desafios: bool = True) -> None:
    """Cierra todas las sesiones de la cuenta y deja sin efecto sus tokens de acceso."""
    ahora = _iso(_ahora())
    c.execute("UPDATE usuario SET version_token = version_token + 1, actualizado_utc = ? WHERE id = ?", (ahora, usuario_id))
    c.execute("UPDATE sesion SET revocado_utc = ? WHERE usuario_id = ? AND revocado_utc IS NULL", (ahora, usuario_id))
    if borrar_desafios:
        c.execute("DELETE FROM desafio WHERE usuario_id = ?", (usuario_id,))


# ----------------------------------------------------------------------
# Cuenta principal y configuración inicial
# ----------------------------------------------------------------------


def asegurar_principal(c) -> None:
    """La cuenta principal existe siempre (sin contraseña hasta que se configure)."""
    email = _normalizar_email(settings.CUENTA_PRINCIPAL)
    ahora = _iso(_ahora())
    actual = c.execute("SELECT id, email, nombre FROM usuario WHERE principal = 1").fetchone()
    if actual:
        # Cambiaron CUENTA_PRINCIPAL: la cuenta principal pasa al correo nuevo (si nadie más lo usa).
        if actual["email"].lower() != email:
            if c.execute("SELECT 1 FROM usuario WHERE email = ? AND id <> ?", (email, actual["id"])).fetchone():
                log.warning("CUENTA_PRINCIPAL=%s ya es de otra cuenta: la principal sigue siendo %s.", email, actual["email"])
                return
            nombre = email.split("@")[0] if actual["nombre"] == actual["email"].split("@")[0] else actual["nombre"]
            c.execute("UPDATE usuario SET email = ?, nombre = ?, version_token = version_token + 1, actualizado_utc = ? WHERE id = ?",
                      (email, nombre, ahora, actual["id"]))
            c.execute("UPDATE sesion SET revocado_utc = ? WHERE usuario_id = ? AND revocado_utc IS NULL", (ahora, actual["id"]))
            auditoria.registrar(c, SISTEMA, "cuenta_principal_cambio_correo", anterior=actual["email"], email=email)
        return
    existente = c.execute("SELECT id FROM usuario WHERE email = ?", (email,)).fetchone()
    if existente:
        c.execute("UPDATE usuario SET principal = 1, gestiona_usuarios = 1, rol = 'supervisor', activo = 1, actualizado_utc = ? "
                  "WHERE id = ?", (ahora, existente["id"]))
    else:
        c.execute(
            "INSERT INTO usuario (id, email, nombre, rol, principal, gestiona_usuarios, creado_por, creado_utc, actualizado_utc) "
            "VALUES (?, ?, ?, 'supervisor', 1, 1, 'sistema', ?, ?)",
            (uuid.uuid4().hex[:12], email, email.split("@")[0], ahora, ahora),
        )


def necesita_configuracion() -> bool:
    with transaccion(escritura=False) as c:
        fila = c.execute("SELECT hash_clave FROM usuario WHERE principal = 1").fetchone()
        return fila is not None and not fila["hash_clave"]


def emitir_enlace_configuracion(horas: int = 24) -> str | None:
    """
    Si la cuenta principal no tiene contraseña, un enlace de un solo uso para
    elegirla. Va en el fragmento (#) de la URL: el navegador no lo manda a ningún
    servidor ni queda en logs. Cada enlace nuevo invalida los anteriores.
    """
    if not necesita_configuracion():
        return None
    token = S.token_opaco()
    with transaccion() as c:
        c.execute("DELETE FROM configuracion_inicial")
        c.execute("INSERT INTO configuracion_inicial VALUES (?, ?)", (S.hash_token(token), _iso(_ahora() + timedelta(hours=horas))))
    return f"{settings.FRONT_URL.rstrip('/')}/configurar#{token}"


def anunciar_configuracion() -> None:
    enlace = emitir_enlace_configuracion()
    if enlace:
        email = _normalizar_email(settings.CUENTA_PRINCIPAL)
        log.warning("\n%s\n  La cuenta principal (%s) todavía no tiene contraseña.\n  Abrí este enlace para elegirla "
                    "(sirve una vez, 24 h):\n\n  %s\n%s", "=" * 78, email, enlace, "=" * 78)


def configurar_principal(token: str, clave: str, quien: Usuario) -> None:
    _limitar(quien.ip)
    with transaccion() as c:
        fila_token = c.execute("SELECT * FROM configuracion_inicial WHERE hash_token = ?", (S.hash_token(token or ""),)).fetchone()
        principal = c.execute("SELECT * FROM usuario WHERE principal = 1").fetchone()
        if not fila_token or _vencido(fila_token["expira_utc"]) or not principal or principal["hash_clave"]:
            auditoria.registrar(c, quien, "configuracion_rechazada")
            rechazado = True
        else:
            rechazado = False
    if rechazado:
        raise _error(400, "ENLACE_INVALIDO", "El enlace no sirve (venció o ya se usó). Reiniciá el backend para que muestre uno nuevo.")
    with transaccion() as c:
        principal = c.execute("SELECT * FROM usuario WHERE principal = 1").fetchone()
        if not c.execute("SELECT 1 FROM configuracion_inicial WHERE hash_token = ?", (S.hash_token(token),)).fetchone() or principal["hash_clave"]:
            raise _error(400, "ENLACE_INVALIDO", "El enlace ya se usó.")
        motivo = S.politica_clave(clave, principal["email"], principal["nombre"])
        if motivo:
            raise _error(422, "CLAVE_DEBIL", motivo)
        c.execute("UPDATE usuario SET hash_clave = ?, debe_cambiar_clave = 0, intentos_fallidos = 0, bloqueado_hasta = NULL, "
                  "actualizado_utc = ? WHERE id = ?", (S.hashear_clave(clave), _iso(_ahora()), principal["id"]))
        c.execute("DELETE FROM configuracion_inicial")
        auditoria.registrar(c, _usuario(principal, quien.ip, quien.user_agent), "cuenta_principal_configurada")


def reiniciar_principal() -> str | None:
    """Para la consola (app/cli.py): quien tiene acceso al servidor recupera la cuenta principal."""
    with transaccion() as c:
        principal = c.execute("SELECT * FROM usuario WHERE principal = 1").fetchone()
        if principal is None:
            asegurar_principal(c)
            principal = c.execute("SELECT * FROM usuario WHERE principal = 1").fetchone()
        _invalidar(c, principal["id"])
        c.execute("UPDATE usuario SET hash_clave = NULL, totp_cifrado = NULL, totp_ultimo_paso = NULL, recuperacion_json = '[]', "
                  "intentos_fallidos = 0, bloqueado_hasta = NULL, activo = 1 WHERE id = ?", (principal["id"],))
        auditoria.registrar(c, SISTEMA, "cuenta_principal_reiniciada", email=principal["email"])
    return emitir_enlace_configuracion()


# ----------------------------------------------------------------------
# Ingreso
# ----------------------------------------------------------------------


def _limitar(ip: str) -> None:
    if not S.limitador_ingreso.permitir(ip or "?"):
        raise _error(429, "DEMASIADOS_INTENTOS", "Demasiados intentos desde esta conexión. Esperá unos minutos.")


def _sumar_fallo(c, fila, quien: Usuario, accion: str) -> None:
    intentos = fila["intentos_fallidos"] + 1
    if intentos >= settings.INTENTOS_MAX:
        hasta = _iso(_ahora() + timedelta(minutes=settings.BLOQUEO_MIN))
        c.execute("UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = ? WHERE id = ?", (hasta, fila["id"]))
        auditoria.registrar(c, quien, "cuenta_bloqueada", hasta=hasta)
    else:
        c.execute("UPDATE usuario SET intentos_fallidos = ? WHERE id = ?", (intentos, fila["id"]))
    auditoria.registrar(c, quien, accion, intento=intentos)


def _exige_2fa(fila) -> bool:
    return settings.DOSFA_SUPERVISORES and fila["rol"] == "supervisor"


def _pasos(fila) -> list[str]:
    pasos = []
    if fila["totp_cifrado"]:
        pasos.append("2fa")  # primero se prueba quién es, después se cambia nada
    if fila["debe_cambiar_clave"]:
        pasos.append("cambiar_clave")
    if not fila["totp_cifrado"] and _exige_2fa(fila):
        pasos.append("enrolar_2fa")
    return pasos


def ingresar(email: str, clave: str, quien: Usuario) -> dict:
    _limitar(quien.ip)
    email = _normalizar_email(email)
    # Los errores se lanzan fuera de la transacción: si no, se desharían el
    # contador de intentos y el registro en la auditoría.
    with transaccion() as c:
        resultado = _ingresar(c, email, clave, quien)
    if isinstance(resultado, ErrorDeLote):
        raise resultado
    return resultado


def _ingresar(c, email: str, clave: str, quien: Usuario) -> dict | ErrorDeLote:
    fila = c.execute("SELECT * FROM usuario WHERE email = ?", (email,)).fetchone()
    correcta = S.verificar_clave(fila["hash_clave"] if fila else None, clave or "")
    if fila is None:
        auditoria.registrar(c, Usuario("", email, frozenset(), quien.ip, quien.user_agent, email), "ingreso_fallido",
                            motivo="correo_inexistente")
        return _credenciales()
    actor = _usuario(fila, quien.ip, quien.user_agent)
    if fila["bloqueado_hasta"] and not _vencido(fila["bloqueado_hasta"]):
        auditoria.registrar(c, actor, "ingreso_fallido", motivo="cuenta_bloqueada")
        return _error(401, "CREDENCIALES", MENSAJE_CREDENCIALES + " Si fallaste varias veces, esperá unos minutos.")
    if not correcta:
        _sumar_fallo(c, fila, actor, "ingreso_fallido")
        return _credenciales()
    if not fila["activo"]:
        auditoria.registrar(c, actor, "ingreso_fallido", motivo="cuenta_desactivada")
        return _credenciales()
    cambios = "intentos_fallidos = 0, bloqueado_hasta = NULL"
    valores: list = []
    if S.necesita_rehash(fila["hash_clave"]):
        cambios += ", hash_clave = ?"
        valores.append(S.hashear_clave(clave))
    c.execute(f"UPDATE usuario SET {cambios} WHERE id = ?", (*valores, fila["id"]))
    pasos = _pasos(fila)
    if not pasos:
        return _abrir_sesion(c, fila, quien)
    token = S.token_opaco()
    c.execute("INSERT INTO desafio (hash_token, usuario_id, pasos_json, expira_utc) VALUES (?, ?, ?, ?)",
              (S.hash_token(token), fila["id"], json.dumps(pasos), _iso(_ahora() + timedelta(minutes=DESAFIO_MIN))))
    auditoria.registrar(c, actor, "clave_correcta", faltan=pasos)
    return {"paso": pasos[0], "desafio": token, "email": fila["email"]}


def _desafio(c, token: str, paso: str):
    fila = c.execute("SELECT * FROM desafio WHERE hash_token = ?", (S.hash_token(token or ""),)).fetchone()
    if fila is None or _vencido(fila["expira_utc"]) or fila["intentos"] >= DESAFIO_INTENTOS:
        if fila is not None:
            c.execute("DELETE FROM desafio WHERE hash_token = ?", (fila["hash_token"],))
        raise _error(401, "DESAFIO_VENCIDO", "El ingreso venció. Empezá de nuevo con tu correo y contraseña.")
    pasos = json.loads(fila["pasos_json"])
    if not pasos or pasos[0] != paso:
        raise _error(409, "PASO_INCORRECTO", "Ese paso no corresponde ahora. Empezá de nuevo.")
    usuario = _fila(c, fila["usuario_id"])
    if usuario is None or not usuario["activo"]:
        raise _credenciales()
    return fila, usuario


def _fallo_desafio(c, desafio, usuario, quien: Usuario, accion: str, mensaje: str) -> ErrorDeLote:
    c.execute("UPDATE desafio SET intentos = intentos + 1 WHERE hash_token = ?", (desafio["hash_token"],))
    _sumar_fallo(c, usuario, _usuario(usuario, quien.ip, quien.user_agent), accion)
    restantes = DESAFIO_INTENTOS - desafio["intentos"] - 1
    return _error(401, "CODIGO_INCORRECTO", mensaje + (f" Te quedan {restantes} intento(s)." if restantes > 0 else " Empezá de nuevo."))


def _avanzar(c, desafio, usuario, quien: Usuario, token: str, **extra) -> dict:
    pasos = json.loads(desafio["pasos_json"])[1:]
    usuario = _fila(c, usuario["id"])  # releída: los pasos la cambian
    if pasos:
        c.execute("UPDATE desafio SET pasos_json = ?, intentos = 0, totp_pendiente = NULL WHERE hash_token = ?",
                  (json.dumps(pasos), desafio["hash_token"]))
        return {"paso": pasos[0], "desafio": token, "email": usuario["email"], **extra}
    c.execute("DELETE FROM desafio WHERE hash_token = ?", (desafio["hash_token"],))
    if not desafio["abre_sesion"]:
        return {"paso": "listo", "usuario": publico(usuario), **extra}
    return {**_abrir_sesion(c, usuario, quien), **extra}


def verificar_2fa(token: str, codigo: str, quien: Usuario) -> dict:
    _limitar(quien.ip)
    with transaccion() as c:
        desafio, usuario = _desafio(c, token, "2fa")
        actor = _usuario(usuario, quien.ip, quien.user_agent)
        secreto = S.descifrar(usuario["totp_cifrado"] or "")
        paso = S.verificar_totp(secreto, codigo or "", usuario["totp_ultimo_paso"]) if secreto else None
        if paso is not None:
            c.execute("UPDATE usuario SET totp_ultimo_paso = ? WHERE id = ?", (paso, usuario["id"]))
            return _avanzar(c, desafio, usuario, quien, token)
        restantes = json.loads(usuario["recuperacion_json"] or "[]")
        huella = S.hash_recuperacion(codigo or "")
        if huella in restantes:
            restantes.remove(huella)
            c.execute("UPDATE usuario SET recuperacion_json = ? WHERE id = ?", (json.dumps(restantes), usuario["id"]))
            auditoria.registrar(c, actor, "codigo_recuperacion_usado", quedan=len(restantes))
            return _avanzar(c, desafio, usuario, quien, token, codigos_recuperacion_restantes=len(restantes))
        error = _fallo_desafio(c, desafio, usuario, quien, "segundo_factor_fallido", "El código no es correcto.")
    raise error


def cambiar_clave_inicial(token: str, nueva: str, quien: Usuario) -> dict:
    with transaccion() as c:
        desafio, usuario = _desafio(c, token, "cambiar_clave")
        _poner_clave(c, usuario, nueva, quien, "clave_cambiada", en_ingreso=True)
        return _avanzar(c, desafio, usuario, quien, token)


def _poner_clave(c, usuario, nueva: str, quien: Usuario, accion: str, en_ingreso: bool = False) -> None:
    motivo = S.politica_clave(nueva, usuario["email"], usuario["nombre"])
    if motivo:
        raise _error(422, "CLAVE_DEBIL", motivo)
    if S.verificar_clave(usuario["hash_clave"], nueva):
        raise _error(422, "CLAVE_REPETIDA", "La contraseña nueva tiene que ser distinta de la anterior.")
    _invalidar(c, usuario["id"], borrar_desafios=not en_ingreso)
    c.execute("UPDATE usuario SET hash_clave = ?, debe_cambiar_clave = 0 WHERE id = ?", (S.hashear_clave(nueva), usuario["id"]))
    auditoria.registrar(c, _usuario(usuario, quien.ip, quien.user_agent), accion)


def iniciar_enrolamiento(token: str) -> dict:
    with transaccion() as c:
        desafio, usuario = _desafio(c, token, "enrolar_2fa")
        secreto = S.descifrar(desafio["totp_pendiente"] or "") or S.nuevo_secreto_totp()
        c.execute("UPDATE desafio SET totp_pendiente = ? WHERE hash_token = ?", (S.cifrar(secreto), desafio["hash_token"]))
        uri = S.uri_totp(secreto, usuario["email"])
        return {"qr": S.qr_png(uri), "secreto": " ".join(secreto[i:i + 4] for i in range(0, len(secreto), 4)), "uri": uri}


def confirmar_enrolamiento(token: str, codigo: str, quien: Usuario, solo_de: str | None = None) -> dict:
    _limitar(quien.ip)
    with transaccion() as c:
        desafio, usuario = _desafio(c, token, "enrolar_2fa")
        if solo_de is not None and usuario["id"] != solo_de:
            raise _error(403, "SIN_PERMISO", "Ese pedido no es tuyo.")
        secreto = S.descifrar(desafio["totp_pendiente"] or "")
        paso = S.verificar_totp(secreto, codigo or "", None) if secreto else None
        if paso is None:
            error = _fallo_desafio(c, desafio, usuario, quien, "segundo_factor_fallido",
                                   "El código no coincide. Revisá que la hora del celular esté bien.")
        else:
            codigos = S.codigos_recuperacion()
            c.execute("UPDATE usuario SET totp_cifrado = ?, totp_ultimo_paso = ?, recuperacion_json = ?, actualizado_utc = ? WHERE id = ?",
                      (S.cifrar(secreto), paso, json.dumps([S.hash_recuperacion(x) for x in codigos]), _iso(_ahora()), usuario["id"]))
            auditoria.registrar(c, _usuario(usuario, quien.ip, quien.user_agent), "segundo_factor_activado")
            return _avanzar(c, desafio, usuario, quien, token, codigos_recuperacion=codigos)
    raise error


# ----------------------------------------------------------------------
# Sesiones
# ----------------------------------------------------------------------


def _abrir_sesion(c, usuario, quien: Usuario, familia: str | None = None, vence_absoluto: str | None = None) -> dict:
    ahora = _ahora()
    nueva = familia is None
    familia = familia or uuid.uuid4().hex
    absoluto = vence_absoluto or _iso(ahora + timedelta(hours=settings.SESION_HORAS))
    expira = min(_iso(ahora + timedelta(minutes=settings.INACTIVIDAD_MIN)), absoluto)
    renovacion = S.token_opaco()
    c.execute("INSERT INTO sesion (hash_token, familia, usuario_id, creado_utc, expira_utc, vence_absoluto_utc, ip, user_agent) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (S.hash_token(renovacion), familia, usuario["id"], _iso(ahora), expira, absoluto, quien.ip, quien.user_agent))
    acceso, vence = S.emitir_acceso(usuario["id"], usuario["version_token"], familia)
    if nueva:
        c.execute("UPDATE usuario SET ultimo_ingreso_utc = ? WHERE id = ?", (_iso(ahora), usuario["id"]))
        auditoria.registrar(c, _usuario(usuario, quien.ip, quien.user_agent, familia), "ingreso")
    return {"paso": "listo", "acceso": acceso, "acceso_vence": vence, "usuario": publico(_fila(c, usuario["id"])),
            "_renovacion": renovacion, "_renovacion_vence": absoluto}


def renovar(token: str, quien: Usuario) -> dict:
    with transaccion() as c:
        fila = c.execute("SELECT * FROM sesion WHERE hash_token = ?", (S.hash_token(token or ""),)).fetchone()
        if fila is None or fila["revocado_utc"]:
            raise _error(401, "SIN_SESION", "Tu sesión terminó. Volvé a ingresar.")
        usuario = _fila(c, fila["usuario_id"])
        actor = _usuario(usuario, quien.ip, quien.user_agent, fila["familia"])
        if fila["usado_utc"]:
            if _iso(_ahora() - timedelta(seconds=GRACIA_RENOVACION_S)) < fila["usado_utc"]:
                raise _error(409, "RENOVANDO", "Otra pestaña está renovando la sesión.")
            c.execute("UPDATE sesion SET revocado_utc = ? WHERE familia = ?", (_iso(_ahora()), fila["familia"]))
            auditoria.registrar(c, actor, "token_reutilizado", familia=fila["familia"])
            error = _error(401, "SIN_SESION", "Por seguridad cerramos tu sesión. Volvé a ingresar.")
        elif _vencido(fila["expira_utc"]) or _vencido(fila["vence_absoluto_utc"]) or not usuario["activo"]:
            c.execute("UPDATE sesion SET revocado_utc = ? WHERE familia = ?", (_iso(_ahora()), fila["familia"]))
            error = _error(401, "SIN_SESION", "Tu sesión venció. Volvé a ingresar.")
        else:
            c.execute("UPDATE sesion SET usado_utc = ? WHERE hash_token = ?", (_iso(_ahora()), fila["hash_token"]))
            return _abrir_sesion(c, usuario, quien, fila["familia"], fila["vence_absoluto_utc"])
    raise error


def salir(token: str | None, quien: Usuario | None) -> None:
    with transaccion() as c:
        familia = quien.sesion if quien and quien.sesion else None
        if token and not familia:
            fila = c.execute("SELECT familia FROM sesion WHERE hash_token = ?", (S.hash_token(token),)).fetchone()
            familia = fila["familia"] if fila else None
        if not familia:
            return
        fila = c.execute("SELECT usuario_id FROM sesion WHERE familia = ? LIMIT 1", (familia,)).fetchone()
        c.execute("UPDATE sesion SET revocado_utc = ? WHERE familia = ? AND revocado_utc IS NULL", (_iso(_ahora()), familia))
        usuario = _fila(c, fila["usuario_id"]) if fila else None
        if usuario:
            ip, ua = (quien.ip, quien.user_agent) if quien else ("", "")
            auditoria.registrar(c, _usuario(usuario, ip, ua, familia), "salida")


def usuario_del_token(token: str, ip: str, user_agent: str) -> Usuario | None:
    datos = S.leer_acceso(token)
    if datos is None:
        return None
    with transaccion(escritura=False) as c:
        fila = _fila(c, str(datos["sub"]))
        if fila is None or not fila["activo"] or fila["version_token"] != datos["ver"]:
            return None
        viva = c.execute("SELECT 1 FROM sesion WHERE familia = ? AND revocado_utc IS NULL LIMIT 1", (datos["sid"],)).fetchone()
        if not viva:
            return None
        return _usuario(fila, ip, user_agent, datos["sid"])


# ----------------------------------------------------------------------
# Mi cuenta
# ----------------------------------------------------------------------


def yo(usuario: Usuario) -> dict:
    with transaccion(escritura=False) as c:
        return publico(_fila(c, usuario.id))


def cambiar_clave(usuario: Usuario, actual: str, nueva: str) -> dict:
    """Cierra todas las sesiones (incluidas las de otros equipos) y abre una nueva acá."""
    with transaccion() as c:
        fila = _fila(c, usuario.id)
        if not S.verificar_clave(fila["hash_clave"], actual or ""):
            _sumar_fallo(c, fila, usuario, "cambio_clave_fallido")
            error = _error(422, "CLAVE_ACTUAL", "La contraseña actual no es correcta.")
        else:
            _poner_clave(c, fila, nueva, usuario, "clave_cambiada")
            return _abrir_sesion(c, _fila(c, usuario.id), usuario)
    raise error


def iniciar_mi_2fa(usuario: Usuario) -> dict:
    with transaccion() as c:
        if _fila(c, usuario.id)["totp_cifrado"]:
            raise _error(409, "YA_ACTIVO", "Ya tenés el segundo factor activado.")
        token = S.token_opaco()
        c.execute("DELETE FROM desafio WHERE usuario_id = ? AND abre_sesion = 0", (usuario.id,))
        c.execute("INSERT INTO desafio (hash_token, usuario_id, pasos_json, abre_sesion, expira_utc) VALUES (?, ?, ?, 0, ?)",
                  (S.hash_token(token), usuario.id, json.dumps(["enrolar_2fa"]), _iso(_ahora() + timedelta(minutes=DESAFIO_MIN))))
    return {"desafio": token, **iniciar_enrolamiento(token)}


def desactivar_mi_2fa(usuario: Usuario, clave: str) -> None:
    with transaccion() as c:
        fila = _fila(c, usuario.id)
        if _exige_2fa(fila):
            raise _error(403, "OBLIGATORIO", "Los supervisores tienen que usar segundo factor.")
        if not S.verificar_clave(fila["hash_clave"], clave or ""):
            _sumar_fallo(c, fila, usuario, "cambio_2fa_fallido")
            error = _error(422, "CLAVE_ACTUAL", "La contraseña no es correcta.")
        else:
            c.execute("UPDATE usuario SET totp_cifrado = NULL, totp_ultimo_paso = NULL, recuperacion_json = '[]' WHERE id = ?", (usuario.id,))
            auditoria.registrar(c, usuario, "segundo_factor_desactivado")
            return
    raise error


# ----------------------------------------------------------------------
# Gestión de usuarios (cuenta principal y las que ella habilite)
# ----------------------------------------------------------------------


def _exigir_gestor(usuario: Usuario) -> None:
    if not usuario.puede_gestionar:
        raise _error(403, "SIN_PERMISO", "Solo la cuenta principal (o quien ella habilite) gestiona usuarios.")


def _objetivo(c, gestor: Usuario, usuario_id: str):
    fila = _fila(c, usuario_id)
    if fila is None:
        raise _error(404, "NO_EXISTE", "La cuenta no existe.")
    if fila["id"] == gestor.id:
        raise _error(409, "PROPIA", "Tu propia cuenta se cambia desde «Mi cuenta».")
    if fila["principal"]:
        raise _error(403, "PRINCIPAL", "La cuenta principal solo se recupera desde la consola del servidor.")
    if fila["gestiona_usuarios"] and not gestor.principal:
        raise _error(403, "SIN_PERMISO", "Solo la cuenta principal cambia a otro gestor de usuarios.")
    return fila


def listar_usuarios(gestor: Usuario) -> list[dict]:
    _exigir_gestor(gestor)
    with transaccion(escritura=False) as c:
        return [publico(f) for f in c.execute("SELECT * FROM usuario ORDER BY principal DESC, activo DESC, nombre COLLATE NOCASE")]


def crear_usuario(gestor: Usuario, email: str, nombre: str, rol: str) -> dict:
    _exigir_gestor(gestor)
    email = _normalizar_email(email)
    nombre = " ".join((nombre or "").split())[:80]
    if not _EMAIL.match(email) or len(email) > 254:
        raise _error(422, "EMAIL_INVALIDO", "El correo no es válido.")
    if not nombre:
        raise _error(422, "NOMBRE", "Poné el nombre de la persona.")
    if rol not in ("empleado", "supervisor"):
        raise _error(422, "ROL", "El rol es empleado o supervisor.")
    temporal = S.clave_temporal()
    with transaccion() as c:
        if c.execute("SELECT 1 FROM usuario WHERE email = ?", (email,)).fetchone():
            raise _error(409, "EMAIL_REPETIDO", "Ya hay una cuenta con ese correo.")
        nuevo_id = uuid.uuid4().hex[:12]
        ahora = _iso(_ahora())
        c.execute("INSERT INTO usuario (id, email, nombre, rol, hash_clave, debe_cambiar_clave, creado_por, creado_utc, actualizado_utc) "
                  "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)", (nuevo_id, email, nombre, rol, S.hashear_clave(temporal), gestor.id, ahora, ahora))
        auditoria.registrar(c, gestor, "usuario_creado", cuenta=email, rol=rol)
        return {"usuario": publico(_fila(c, nuevo_id)), "clave_temporal": temporal}


def actualizar_usuario(gestor: Usuario, usuario_id: str, nombre: str | None, rol: str | None, activo: bool | None,
                       gestiona_usuarios: bool | None) -> dict:
    _exigir_gestor(gestor)
    with transaccion() as c:
        fila = _objetivo(c, gestor, usuario_id)
        antes = publico(fila)
        cambios: dict = {}
        if nombre is not None and " ".join(nombre.split()):
            cambios["nombre"] = " ".join(nombre.split())[:80]
        if rol is not None:
            if rol not in ("empleado", "supervisor"):
                raise _error(422, "ROL", "El rol es empleado o supervisor.")
            cambios["rol"] = rol
        if activo is not None:
            cambios["activo"] = int(activo)
        if gestiona_usuarios is not None:
            if not gestor.principal:
                raise _error(403, "SIN_PERMISO", "Solo la cuenta principal habilita a otro a gestionar usuarios.")
            if gestiona_usuarios and (cambios.get("rol", fila["rol"]) != "supervisor"):
                raise _error(422, "ROL", "Para gestionar usuarios tiene que ser supervisor.")
            cambios["gestiona_usuarios"] = int(gestiona_usuarios)
        if cambios.get("rol") == "empleado" and fila["gestiona_usuarios"]:
            cambios["gestiona_usuarios"] = 0
        if not cambios:
            return antes
        sets = ", ".join(f"{k} = ?" for k in cambios)
        c.execute(f"UPDATE usuario SET {sets}, actualizado_utc = ? WHERE id = ?", (*cambios.values(), _iso(_ahora()), usuario_id))
        # Cambió lo que puede hacer: que vuelva a ingresar.
        if any(k in cambios for k in ("rol", "activo", "gestiona_usuarios")):
            _invalidar(c, usuario_id)
        despues = publico(_fila(c, usuario_id))
        auditoria.registrar(c, gestor, "usuario_actualizado", cuenta=fila["email"],
                            antes={k: antes[k] for k in antes if antes[k] != despues[k]},
                            despues={k: despues[k] for k in despues if antes[k] != despues[k]})
        return despues


def resetear_clave(gestor: Usuario, usuario_id: str) -> dict:
    _exigir_gestor(gestor)
    temporal = S.clave_temporal()
    with transaccion() as c:
        fila = _objetivo(c, gestor, usuario_id)
        _invalidar(c, usuario_id)
        c.execute("UPDATE usuario SET hash_clave = ?, debe_cambiar_clave = 1, intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?",
                  (S.hashear_clave(temporal), usuario_id))
        auditoria.registrar(c, gestor, "clave_reseteada", cuenta=fila["email"])
        return {"usuario": publico(_fila(c, usuario_id)), "clave_temporal": temporal}


def resetear_2fa(gestor: Usuario, usuario_id: str) -> dict:
    _exigir_gestor(gestor)
    with transaccion() as c:
        fila = _objetivo(c, gestor, usuario_id)
        _invalidar(c, usuario_id)
        c.execute("UPDATE usuario SET totp_cifrado = NULL, totp_ultimo_paso = NULL, recuperacion_json = '[]', intentos_fallidos = 0, "
                  "bloqueado_hasta = NULL WHERE id = ?", (usuario_id,))
        auditoria.registrar(c, gestor, "segundo_factor_reseteado", cuenta=fila["email"])
        return publico(_fila(c, usuario_id))


def desbloquear(gestor: Usuario, usuario_id: str) -> dict:
    _exigir_gestor(gestor)
    with transaccion() as c:
        fila = _objetivo(c, gestor, usuario_id)
        c.execute("UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?", (usuario_id,))
        auditoria.registrar(c, gestor, "cuenta_desbloqueada", cuenta=fila["email"])
        return publico(_fila(c, usuario_id))
