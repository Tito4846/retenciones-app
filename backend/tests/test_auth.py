"""El ingreso de verdad: contraseña, tokens, segundo factor, bloqueos y gestión de usuarios."""

import time

import pytest

from app.core import seguridad as S
from app.core.config import settings
from app.services import cuentas
from tests.test_api import cliente

pytestmark = pytest.mark.login_real

PRINCIPAL = "tito.rodriguez@estudiopiccinini.com.ar"
CLAVE = "una frase larga y segura 2026"
CSRF = {"X-Requested-With": "retenciones"}


@pytest.fixture(autouse=True)
def datos_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DIRECTORIO_DATOS", tmp_path / "datos")
    monkeypatch.setattr(settings, "CUENTA_PRINCIPAL", PRINCIPAL)
    # El cliente de tests (http://testserver) no manda cookies Secure; el navegador sí en localhost.
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    # La mayoría de los tests recorre el segundo factor obligatorio; el que lo prueba apagado lo cambia.
    monkeypatch.setattr(settings, "DOSFA_SUPERVISORES", True)
    cliente.cookies.clear()


def _codigo_actual(secreto: str, desfase: int = 0) -> str:
    return S._codigo(secreto.replace(" ", ""), int(time.time()) // S.PASO_TOTP + desfase)


def _configurar_principal() -> None:
    enlace = cuentas.emitir_enlace_configuracion()
    token = enlace.split("#", 1)[1]
    assert cliente.post("/api/auth/configurar", json={"token": token, "clave": CLAVE}).status_code == 204


def _ingresar_principal() -> tuple[dict, str]:
    """Hace todo el recorrido de la primera vez (contraseña + alta del 2FA). Devuelve la sesión y el secreto TOTP."""
    _configurar_principal()
    r = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": CLAVE}).json()
    assert r["paso"] == "enrolar_2fa"
    qr = cliente.post("/api/auth/2fa/enrolar", json={"desafio": r["desafio"]}).json()
    assert qr["qr"].startswith("data:image/png;base64,")
    sesion = cliente.post("/api/auth/2fa/confirmar", json={"desafio": r["desafio"], "codigo": _codigo_actual(qr["secreto"])}).json()
    assert sesion["paso"] == "listo" and len(sesion["codigos_recuperacion"]) == 10
    return sesion, qr["secreto"]


def _bearer(sesion: dict) -> dict:
    return {"Authorization": f"Bearer {sesion['acceso']}"}


def test_configuracion_inicial_con_enlace_de_un_solo_uso():
    assert cliente.get("/api/auth/estado").json()["configurada"] is False
    enlace = cuentas.emitir_enlace_configuracion()
    assert enlace.startswith(settings.FRONT_URL) and "#" in enlace
    token = enlace.split("#", 1)[1]
    # Contraseña débil: no.
    assert cliente.post("/api/auth/configurar", json={"token": token, "clave": "corta"}).status_code == 422
    assert cliente.post("/api/auth/configurar", json={"token": token, "clave": CLAVE}).status_code == 204
    assert cliente.get("/api/auth/estado").json()["configurada"] is True
    # El mismo enlace no sirve dos veces, ni uno inventado.
    assert cliente.post("/api/auth/configurar", json={"token": token, "clave": CLAVE + "x"}).status_code == 400
    assert cliente.post("/api/auth/configurar", json={"token": "x" * 40, "clave": CLAVE}).status_code == 400


def test_cambiar_cuenta_principal_pasa_la_cuenta_al_correo_nuevo(monkeypatch):
    from app.core.db import transaccion

    _configurar_principal()
    monkeypatch.setattr(settings, "CUENTA_PRINCIPAL", "Otra.Persona@Estudio.com.ar")
    with transaccion() as c:
        cuentas.asegurar_principal(c)
        filas = c.execute("SELECT email, nombre, hash_clave FROM usuario WHERE principal = 1").fetchall()
    assert len(filas) == 1 and filas[0]["email"] == "otra.persona@estudio.com.ar" and filas[0]["nombre"] == "otra.persona"
    assert filas[0]["hash_clave"]  # conserva la contraseña
    assert cliente.post("/api/auth/ingresar", json={"email": "otra.persona@estudio.com.ar", "clave": CLAVE}).json()["paso"] == "enrolar_2fa"


def test_sin_dosfa_obligatorio_el_supervisor_entra_solo_con_la_contrasena(monkeypatch):
    monkeypatch.setattr(settings, "DOSFA_SUPERVISORES", False)
    _configurar_principal()
    r = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": CLAVE}).json()
    assert r["paso"] == "listo" and r["usuario"]["exige_2fa"] is False
    # Lo puede activar y desactivar desde Mi cuenta.
    qr = cliente.post("/api/auth/mi-2fa", headers=_bearer(r)).json()
    cod = cliente.post("/api/auth/mi-2fa/confirmar", headers=_bearer(r), json={"desafio": qr["desafio"], "codigo": _codigo_actual(qr["secreto"])})
    assert cod.status_code == 200
    assert cliente.post("/api/auth/mi-2fa/desactivar", headers=_bearer(r), json={"clave": CLAVE}).status_code == 204


def test_supervisor_tiene_que_registrar_el_segundo_factor_y_despues_lo_usa():
    sesion, secreto = _ingresar_principal()
    yo = cliente.get("/api/auth/yo", headers=_bearer(sesion)).json()
    assert yo["email"] == PRINCIPAL and yo["principal"] and yo["tiene_2fa"]
    # El token de renovación nunca viaja en el cuerpo: va en la cookie HttpOnly.
    assert "_renovacion" not in sesion and "renovacion" not in str(sesion)
    cliente.post("/api/auth/salir", headers={**CSRF, **_bearer(sesion)})
    # Segunda vez: pide el código.
    r = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": CLAVE}).json()
    assert r["paso"] == "2fa"
    assert cliente.post("/api/auth/2fa", json={"desafio": r["desafio"], "codigo": "000000"}).status_code == 401
    listo = cliente.post("/api/auth/2fa", json={"desafio": r["desafio"], "codigo": _codigo_actual(secreto, 1)}).json()
    assert listo["paso"] == "listo"


def test_codigo_de_recuperacion_sirve_una_sola_vez():
    sesion, _ = _ingresar_principal()
    codigo = sesion["codigos_recuperacion"][0]
    for esperado in ("listo", None):
        r = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": CLAVE}).json()
        resp = cliente.post("/api/auth/2fa", json={"desafio": r["desafio"], "codigo": codigo})
        if esperado:
            assert resp.json()["paso"] == "listo" and resp.json()["codigos_recuperacion_restantes"] == 9
        else:
            assert resp.status_code == 401


def test_mensajes_iguales_y_bloqueo_por_intentos():
    _configurar_principal()
    inexistente = cliente.post("/api/auth/ingresar", json={"email": "nadie@x.com", "clave": "lo que sea 123"})
    mala = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": "otra clave cualquiera"})
    # No se puede saber qué correos tienen cuenta.
    assert inexistente.status_code == mala.status_code == 401
    assert inexistente.json()["detail"]["mensaje"] == mala.json()["detail"]["mensaje"]
    for _ in range(settings.INTENTOS_MAX - 1):
        cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": "otra clave cualquiera"})
    # Bloqueada: ni con la contraseña correcta.
    r = cliente.post("/api/auth/ingresar", json={"email": PRINCIPAL, "clave": CLAVE})
    assert r.status_code == 401
    from app.core.db import transaccion

    with transaccion(escritura=False) as c:
        acciones = [f["action_type"] for f in c.execute("SELECT action_type FROM auditoria")]
    assert "cuenta_bloqueada" in acciones and acciones.count("ingreso_fallido") >= settings.INTENTOS_MAX


def test_limite_por_ip():
    S.limitador_ingreso.limpiar()
    codigos = [cliente.post("/api/auth/ingresar", json={"email": "x@x.com", "clave": "abcdefghijklm"}).status_code for _ in range(25)]
    assert 429 in codigos


def test_renovacion_rota_y_detecta_reuso():
    sesion, _ = _ingresar_principal()
    vieja = cliente.cookies.get("retenciones_renovacion")
    assert vieja
    # Sin el encabezado anti-CSRF no renueva.
    assert cliente.post("/api/auth/renovar").status_code == 403
    nueva = cliente.post("/api/auth/renovar", headers=CSRF)
    assert nueva.status_code == 200 and nueva.json()["acceso"] != sesion["acceso"]
    assert cliente.cookies.get("retenciones_renovacion") != vieja
    # Alguien usa la vieja después del margen de gracia: se cae toda la sesión.
    from app.core.db import transaccion

    with transaccion() as c:
        c.execute("UPDATE sesion SET usado_utc = '2000-01-01T00:00:00Z' WHERE hash_token = ?", (S.hash_token(vieja),))
    cliente.cookies.set("retenciones_renovacion", vieja, path="/api/auth")
    assert cliente.post("/api/auth/renovar", headers=CSRF).status_code == 401
    assert cliente.get("/api/auth/yo", headers=_bearer(nueva.json())).status_code == 401


def test_token_alterado_o_de_otro_servidor_no_entra():
    sesion, _ = _ingresar_principal()
    token = sesion["acceso"]
    alterado = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert cliente.get("/api/auth/yo", headers={"Authorization": f"Bearer {alterado}"}).status_code == 401
    import jwt

    falso = jwt.encode({"sub": sesion["usuario"]["id"], "ver": 1, "sid": "x", "iat": 0, "exp": 9999999999}, "otra", algorithm="HS256")
    assert cliente.get("/api/auth/yo", headers={"Authorization": f"Bearer {falso}"}).status_code == 401
    sin_firma = jwt.encode({"sub": sesion["usuario"]["id"]}, None, algorithm="none")
    assert cliente.get("/api/auth/yo", headers={"Authorization": f"Bearer {sin_firma}"}).status_code == 401


def test_alta_de_empleado_con_clave_temporal_y_revocacion_inmediata():
    principal, _ = _ingresar_principal()
    jefe = _bearer(principal)
    alta = cliente.post("/api/usuarios", json={"email": "Ana@Estudio.com", "nombre": "Ana Gómez", "rol": "empleado"}, headers=jefe)
    assert alta.status_code == 201
    temporal, ana_id = alta.json()["clave_temporal"], alta.json()["usuario"]["id"]
    assert cliente.post("/api/usuarios", json={"email": "ana@estudio.com", "nombre": "Otra", "rol": "empleado"}, headers=jefe).status_code == 409

    cliente.cookies.clear()
    r = cliente.post("/api/auth/ingresar", json={"email": "ana@estudio.com", "clave": temporal}).json()
    assert r["paso"] == "cambiar_clave"  # empleado: sin 2FA obligatorio
    debil = cliente.post("/api/auth/cambiar-clave-inicial", json={"desafio": r["desafio"], "nueva": "anagomez12345"})
    assert debil.status_code == 422
    ana = cliente.post("/api/auth/cambiar-clave-inicial", json={"desafio": r["desafio"], "nueva": "mi clave nueva de ana 77"}).json()
    assert ana["paso"] == "listo" and ana["usuario"]["rol"] == "empleado"
    # Un empleado no gestiona usuarios.
    assert cliente.get("/api/usuarios", headers=_bearer(ana)).status_code == 403
    # La desactivan: su token deja de servir en el acto.
    assert cliente.get("/api/lotes", headers=_bearer(ana)).status_code == 200
    assert cliente.patch(f"/api/usuarios/{ana_id}", json={"activo": False}, headers=jefe).status_code == 200
    assert cliente.get("/api/lotes", headers=_bearer(ana)).status_code == 401


def test_reseteo_de_clave_y_permisos_de_gestion():
    principal, _ = _ingresar_principal()
    jefe = _bearer(principal)
    luis = cliente.post("/api/usuarios", json={"email": "luis@estudio.com", "nombre": "Luis", "rol": "supervisor"}, headers=jefe).json()
    # La principal no se puede tocar desde la API, ni la propia cuenta.
    assert cliente.post(f"/api/usuarios/{principal['usuario']['id']}/resetear-clave", headers=jefe).status_code == 409
    nueva = cliente.post(f"/api/usuarios/{luis['usuario']['id']}/resetear-clave", headers=jefe).json()["clave_temporal"]
    assert nueva != luis["clave_temporal"]
    r = cliente.post("/api/auth/ingresar", json={"email": "luis@estudio.com", "clave": luis["clave_temporal"]})
    assert r.status_code == 401  # la vieja ya no sirve
    r = cliente.post("/api/auth/ingresar", json={"email": "luis@estudio.com", "clave": nueva}).json()
    # Supervisor: cambia la clave y después registra el 2FA.
    assert r["paso"] == "cambiar_clave"
    r = cliente.post("/api/auth/cambiar-clave-inicial", json={"desafio": r["desafio"], "nueva": "el supervisor elige otra 2026"}).json()
    assert r["paso"] == "enrolar_2fa"
    # Solo la principal habilita a otro a gestionar usuarios.
    assert cliente.patch(f"/api/usuarios/{luis['usuario']['id']}", json={"gestiona_usuarios": True}, headers=jefe).json()["gestiona_usuarios"]


def test_hash_de_contrasena_es_argon2id_y_no_guarda_secretos_en_claro():
    _ingresar_principal()
    from app.core.db import transaccion

    with transaccion(escritura=False) as c:
        fila = c.execute("SELECT * FROM usuario WHERE principal = 1").fetchone()
        tokens = [f["hash_token"] for f in c.execute("SELECT hash_token FROM sesion")]
    assert fila["hash_clave"].startswith("$argon2id$")
    assert CLAVE not in str(dict(fila))
    assert not S.descifrar("no es un token") and S.descifrar(fila["totp_cifrado"])  # cifrado, se descifra con la clave del servidor
    assert all(len(t) == 64 for t in tokens)  # solo SHA-256 de los tokens de renovación


def test_encabezados_de_seguridad():
    r = cliente.get("/api/auth/estado")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
