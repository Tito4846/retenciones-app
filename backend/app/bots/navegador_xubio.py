"""
La ventana de Chrome del bot para Xubio ("Conectar Xubio").

Adaptado de Arca-bot (navegador_xubio.py): la persona entra una vez en la
ventana del bot (o el bot escribe el mail y la contrasena en Visma Connect) y
el bot reusa esa sesion. El reCAPTCHA no se toca: si aparece, lo resuelve la
persona en la ventana. Cada dato se escribe UNA vez por ingreso: si Xubio no
deja entrar, no se reintenta (para no bloquear la cuenta).

Lo que hace con la sesion es lo del modulo bancario (xubio_web_client.py): pide
el Libro Mayor por el webreport, el mismo POST XML que arma la web de Xubio.
La diferencia es que aca el pedido sale desde la ventana del bot
(contexto.request comparte sus cookies), asi que no hace falta copiar la
cookie ni el token al .env.

La cuenta de Xubio que manda la pantalla queda SOLO en memoria hasta que se
desconecta. El perfil de Chrome (cookies) queda en datos/xubio_perfil: si Xubio
recuerda el ingreso, despues de reiniciar no hace falta volver a entrar.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as EsperaAgotada
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from app.bots.chrome import (
    ARGS_CHROME,
    ARGS_SIN_FRENO_EN_SEGUNDO_PLANO,
    es_cierre,
    lanzar_con_reintento,
    minimizar_ventana,
    mostrar_ventana,
)
from app.bots import libro_mayor_xubio
from app.bots.bitacora import Bitacora, tipo_de
from app.core.config import settings

logger = logging.getLogger(__name__)

# Cuanto se espera a que la persona entre antes de cerrar la ventana.
ESPERA_INGRESO_S = 15 * 60
ESPERA_CIERRE_S = 15
_VIGILANCIA_S = 1.0
MANTENER_VIVA_S = 4 * 60
# Ingreso de Visma Connect: primero el mail (#Username), despues la contrasena.
_DOMINIO_VISMA = "connect.visma.com"
_SEL_USUARIO = "#Username, input[type=email]"
_SEL_CLAVE = "input[type=password]"
_ESPERA_CAMPO_MS = 5000
ESPERA_RESPUESTA_S = 20
_SEL_CAPTCHA = 'iframe[src*="recaptcha"][src*="bframe"]'

RUTA_WEBREPORT = (
    "/NXV/webreport/data?layout=LibroMayorNXVWebReportGridLayout&fafViewID=50526&view=null"
)

DESCONECTADO = "desconectado"
ESPERANDO = "esperando_ingreso"
CONECTADO = "conectado"

MENSAJE_CAPTCHA = "El bot ya puso el mail y la contraseña de Xubio: esperando que Xubio deje entrar…"
MENSAJE_CAPTCHA_PEDIDO = "Xubio pidió el captcha: resolvelo en la ventana de Chrome del bot."
MENSAJE_RECHAZADO = (
    "Xubio no dejó entrar con ese mail y contraseña: revisalos y probá de nuevo. "
    "(Si la cuenta tiene verificación en dos pasos, completala en la ventana.)"
)
MENSAJE_SIN_CONEXION = "Xubio no está conectado: tocá «Conectar Xubio» en Conexiones."
MENSAJE_CERRADA = "Se cerró la ventana de Xubio: tocá «Conectar Xubio» para volver a entrar."

# Xubio contesta 200 con este aviso cuando la sesion ya no vale (otro
# navegador entro con el mismo usuario).
AVISOS_SESION_INVALIDA = ("Advertencia de Seguridad", "fue cerrada porque ingresaste", "cada usuario puede acceder a una")

_RUTAS_DE_INGRESO = ("newlogin", "/login", "logincommons", "recoverpassword", "registro", "vismaconnect")


class XubioDesconectado(Exception):
    """No hay sesion de Xubio para trabajar."""


class ErrorXubio(RuntimeError):
    """Xubio respondio algo que no sirve. El mensaje es para la pantalla."""


def en_ingreso(url: str) -> bool:
    """
    Si la pestana todavia esta en el ingreso: cualquier sitio que no sea
    xubio.com (connect.visma.com, en blanco) o las paginas de ingreso de Xubio.
    Se mira el dominio y no el texto: la URL de Visma trae "xubio.com" adentro.
    """
    partes = urlparse(url or "")
    dominio = (partes.hostname or "").lower()
    if partes.scheme not in ("http", "https") or not (dominio == "xubio.com" or dominio.endswith(".xubio.com")):
        return True
    return any(marca in partes.path.lower() for marca in _RUTAS_DE_INGRESO)


def payload_libro_mayor(desde: date, hasta: date, cuenta: str) -> str:
    """El POST XML del webreport del Libro Mayor (igual que el modulo bancario)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<postData>
    <userData><![CDATA[]]></userData>
    <parameters>
        <parameter type="FafDate" name="p_FechaDesde" value="{desde.isoformat()}" />
        <parameter type="FafDate" name="p_FechaHasta" value="{hasta.isoformat()}" />
        <parameter type="" name="p_CuentaReportCode" value="{cuenta}" />
        <parameter type="FafBoolean" name="p_SoloConSaldo" value="false" />
        <parameter type="" name="p_OrganizacionReportCode" value="" />
        <parameter type="FafBoolean" name="p_IncluirClientes" value="true" />
        <parameter type="FafBoolean" name="p_IncluirProveedores" value="true" />
        <parameter type="" name="p_MonedaReportCode" value="" />
        <parameter type="" name="p_EmpresaReportCode" value="" />
        <parameter type="FafInteger" name="p_VerPor" value="0" />
        <parameter type="FafBoolean" name="p_SoloCuentasDisponibilidad" value="false" />
        <parameter type="" name="p_circuitoContable" value="-2" />
        <parameter type="FafBoolean" name="p_excluirUltimoCierreResultados" value="false" />
        <parameter type="FafBoolean" name="p_excluirUltimoCierrePatrimonial" value="false" />
    </parameters>
</postData>"""


class NavegadorXubio:
    """
    Uso (desde la API):
        xubio.conectar(usuario, clave)   # abre la ventana; entra el bot o la persona
        xubio.estado()                   # {"estado", "mensaje", "usuario", "cuenta_env"}
        xubio.libro_mayor(desde, hasta)  # XML crudo del webreport
        xubio.desconectar()
    """

    def __init__(self, perfil: Path | None = None, lanzar: Callable[[str], Any] | None = None) -> None:
        self._perfil = perfil or settings.DIRECTORIO_DATOS / "xubio_perfil"
        self._lanzar = lanzar or _ChromeXubio
        self._candado = threading.Lock()
        self._cola: queue.Queue = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._estado = DESCONECTADO
        self._mensaje: str | None = None
        # (mail, contrasena) que mando la pantalla. SOLO en memoria; None = la del .env.
        self._cuenta: tuple[str, str] | None = None
        self._cerrar = False
        # La empresa con la que se entro (la de arriba, junto a «Estudio:»).
        self._empresa_activa: str | None = None
        # Lo que fue pasando, para el mini-log de la pantalla (solo en memoria).
        self._bitacora = Bitacora()

    # API

    def _cuenta_actual(self) -> tuple[str, str] | None:
        if self._cuenta:
            return self._cuenta
        if settings.XUBIO_USUARIO.strip() and settings.XUBIO_CLAVE:
            return settings.XUBIO_USUARIO.strip(), settings.XUBIO_CLAVE
        return None

    def estado(self) -> dict[str, Any]:
        """El estado para la pantalla. Nunca lleva la contrasena."""
        with self._candado:
            cuenta = self._cuenta_actual()
            return {
                "estado": self._estado,
                "mensaje": self._mensaje,
                "usuario": cuenta[0] if cuenta else None,
                "cuenta_env": bool(settings.XUBIO_USUARIO.strip() and settings.XUBIO_CLAVE),
                "empresa_activa": self._empresa_activa,
                **self._bitacora.a_dict(),
            }

    @property
    def conectado(self) -> bool:
        with self._candado:
            return self._estado == CONECTADO

    def conectar(self, usuario: str | None = None, clave: str | None = None) -> dict[str, Any]:
        with self._candado:
            hilo, cerrando = self._hilo, self._cerrar
        if hilo is not None and hilo.is_alive():
            if not cerrando:
                return self.estado()
            hilo.join(timeout=ESPERA_CIERRE_S)
            if hilo.is_alive():
                return {**self.estado(), "mensaje": "La ventana anterior de Xubio todavía se está cerrando: probá en unos segundos."}

        with self._candado:
            self._cuenta = (usuario.strip(), clave) if usuario and usuario.strip() and clave else None
            cuenta = self._cuenta_actual()
            self._estado = ESPERANDO
            self._mensaje = (
                f"Entrando a Xubio como {cuenta[0]}: si aparece el captcha, resolvelo en la ventana de Chrome."
                if cuenta
                else "Entrá con tu usuario de Xubio en la ventana de Chrome que se abrió."
            )
            self._bitacora.registrar("Abriendo Xubio…", "info")
            self._cerrar = False
            self._cola = queue.Queue()
            self._hilo = threading.Thread(target=self._bucle, name="navegador-xubio", daemon=True)
            self._hilo.start()
        return self.estado()

    def desconectar(self) -> None:
        """Cierra la ventana. El perfil queda: Xubio puede recordar el ingreso."""
        with self._candado:
            if self._estado != DESCONECTADO:
                self._bitacora.registrar("Desconectado a pedido: el bot dejó de trabajar.", "info")
            self._cerrar = True
            self._cuenta = None
            self._estado, self._mensaje = DESCONECTADO, None
        self._cola.put(None)

    def mostrar(self) -> None:
        self.ejecutar(lambda pestana, contexto: mostrar_ventana(contexto, pestana))

    def ejecutar(self, fn: Callable[[Any, Any], Any]) -> Any:
        """Corre fn(pestana, contexto) en el hilo del navegador. Sin sesion: XubioDesconectado."""
        with self._candado:
            if self._estado != CONECTADO or self._hilo is None or not self._hilo.is_alive():
                raise XubioDesconectado(self._mensaje if self._estado == ESPERANDO else MENSAJE_SIN_CONEXION)
            hilo = self._hilo
        futuro: Future = Future()
        self._cola.put((fn, futuro))
        while True:
            try:
                return futuro.result(timeout=_VIGILANCIA_S)
            except EsperaAgotada:
                if not hilo.is_alive() and not futuro.done():
                    raise XubioDesconectado(MENSAJE_SIN_CONEXION) from None

    def libro_mayor(self, desde: date, hasta: date) -> str:
        """
        El XML del webreport del Libro Mayor entre dos fechas, pedido con la
        sesion de la ventana. El parseo vive en importador_mayor.
        """
        payload = payload_libro_mayor(desde, hasta, settings.XUBIO_CUENTA_REPORT_CODE)

        def pedir(pestana: Any, contexto: Any) -> str:
            base = settings.XUBIO_WEB_URL.rstrip("/")
            if not base:
                partes = urlparse(pestana.url)
                base = f"{partes.scheme}://{partes.netloc}"
            respuesta = contexto.request.post(
                base + RUTA_WEBREPORT,
                data=payload,
                headers={"Content-Type": "application/xml"},
                timeout=120000,
            )
            texto = respuesta.text()
            if not respuesta.ok:
                raise ErrorXubio(f"Xubio no devolvió el Libro Mayor (HTTP {respuesta.status}).")
            if any(aviso in texto[:4000] for aviso in AVISOS_SESION_INVALIDA):
                raise XubioDesconectado(
                    "Xubio cerró la sesión del bot porque alguien entró con el mismo usuario "
                    "desde otro navegador. Volvé a conectar Xubio."
                )
            return texto

        return self.ejecutar(pedir)

    def libro_mayor_por_pantalla(
        self,
        desde: date,
        hasta: date,
        impuesto: str,
        tipo: str,
        avance: Callable[[str], None] = lambda detalle: None,
    ) -> list[tuple[str, str]]:
        """
        El recorrido de una persona: Libro Mayor, «Elige los filtros» con las
        fechas, en «Cuenta» la del impuesto y el tipo de ARCA, Aceptar. Devuelve
        [(cuenta, reporte)]. Si la pantalla no responde, guarda una captura en
        datos/debug y sale ErrorPantallaXubio.
        """

        def recorrer(pestana: Any, contexto: Any) -> list[tuple[str, str]]:
            try:
                return libro_mayor_xubio.recorrer(pestana, desde, hasta, impuesto, tipo, avance)
            except Exception as error:
                if es_cierre(error):
                    raise
                from app.bots.navegador_arca import capturar_paginas

                capturar_paginas(contexto, settings.DIRECTORIO_DATOS / "debug", "xubio_libro_mayor")
                if isinstance(error, libro_mayor_xubio.ErrorPantallaXubio):
                    raise
                raise libro_mayor_xubio.ErrorPantallaXubio(f"La pantalla de Libro Mayor no respondió: {error}") from error

        return self.ejecutar(recorrer)

    # hilo del navegador

    def _fijar(self, estado: str, mensaje: str | None) -> None:
        with self._candado:
            if self._cerrar:
                return
            if (self._estado, self._mensaje) != (estado, mensaje):
                logger.info("Xubio: %s%s", estado, f" ({mensaje})" if mensaje else "")
                self._bitacora.registrar(mensaje or ("Conectado a Xubio." if estado == CONECTADO else estado), tipo_de(estado, mensaje))
            elif estado == CONECTADO:
                self._bitacora.tocar()  # el bot volvió a ver la sesión abierta
            self._estado, self._mensaje = estado, mensaje

    def _olvidar_cuenta(self) -> None:
        with self._candado:
            self._cuenta = None

    def _bucle(self) -> None:
        try:
            with self._lanzar(str(self._perfil)) as (contexto, pestana):
                self._vigilar(contexto, pestana)
        except BaseException as error:  # noqa: BLE001 - Chrome puede fallar al abrir o caerse
            if es_cierre(error):
                self._fijar(DESCONECTADO, MENSAJE_CERRADA)
            else:
                logger.exception("La ventana de Xubio fallo")
                self._fijar(DESCONECTADO, f"No se pudo abrir Chrome para Xubio: {error}")
        finally:
            self._vaciar_cola()

    def _vigilar(self, contexto: Any, pestana: Any) -> None:
        limite = time.monotonic() + ESPERA_INGRESO_S
        minimizada = False
        escrito: set[str] = set()
        escrito_en: float | None = None
        captcha_avisado = False
        ultimo_uso = time.monotonic()
        while True:
            # La persona puede abrir otras pestanas: se trabaja con la ultima de Xubio.
            pestana = _pestana_de_xubio(contexto) or pestana
            if pestana.is_closed():
                self._fijar(DESCONECTADO, MENSAJE_CERRADA)
                return
            with self._candado:
                cuenta = self._cuenta_actual()
            if en_ingreso(pestana.url):
                if self.conectado:
                    limite = time.monotonic() + ESPERA_INGRESO_S
                    escrito.clear()
                    escrito_en, captcha_avisado = None, False
                    self._fijar(ESPERANDO, "Xubio cerró la sesión: el bot vuelve a entrar." if cuenta else "Xubio cerró la sesión: volvé a entrar en la ventana del bot.")
                    if cuenta:
                        _volver_al_ingreso(pestana)
                elif time.monotonic() > limite:
                    self._fijar(DESCONECTADO, "No se entró a Xubio a tiempo: se cerró la ventana.")
                    return
                if cuenta:
                    hecho = _completar_ingreso(pestana, cuenta, escrito)
                    if hecho is not None:
                        escrito_en = time.monotonic()
                        if hecho == "clave":
                            self._fijar(ESPERANDO, MENSAJE_CAPTCHA)
                    elif escrito_en is not None and time.monotonic() - escrito_en > ESPERA_RESPUESTA_S:
                        if _hay_captcha(pestana):
                            if not captcha_avisado:
                                captcha_avisado = True
                                self._fijar(ESPERANDO, MENSAJE_CAPTCHA_PEDIDO)
                        else:
                            # No se reintenta (no bloquear la cuenta): se olvida y la pantalla la pide de nuevo.
                            self._olvidar_cuenta()
                            self._fijar(DESCONECTADO, MENSAJE_RECHAZADO)
                            return
            elif not self.conectado:
                self._fijar(CONECTADO, None)
                ultimo_uso = time.monotonic()
                self._empresa_activa = libro_mayor_xubio.empresa_activa(pestana) or self._empresa_activa
                if not minimizada and settings.MINIMIZAR_VENTANA:
                    minimizada = minimizar_ventana(contexto, pestana)
            elif time.monotonic() - ultimo_uso > MANTENER_VIVA_S:
                ultimo_uso = time.monotonic()
                if not _mantener_viva(contexto, pestana):
                    # A la pantalla de ingreso: la vuelta siguiente entra sola con la misma cuenta.
                    _volver_al_ingreso(pestana)

            try:
                item = self._cola.get(timeout=_VIGILANCIA_S)
            except queue.Empty:
                continue
            if item is None:
                return
            fn, futuro = item
            if not futuro.set_running_or_notify_cancel():
                continue
            try:
                futuro.set_result(fn(pestana, contexto))
            except BaseException as error:  # noqa: BLE001 - viaja al hilo que pidio
                if es_cierre(error):
                    futuro.set_exception(XubioDesconectado(MENSAJE_SIN_CONEXION))
                    raise
                futuro.set_exception(error)
            finally:
                ultimo_uso = time.monotonic()

    def _vaciar_cola(self) -> None:
        while True:
            try:
                item = self._cola.get_nowait()
            except queue.Empty:
                return
            if item is not None and not item[1].done():
                item[1].set_exception(XubioDesconectado(MENSAJE_SIN_CONEXION))


def _completar_ingreso(pestana: Any, cuenta: tuple[str, str], escrito: set[str]) -> str | None:
    """Escribe en Visma Connect el dato que pida la pagina, una sola vez por ingreso."""
    if (urlparse(pestana.url or "").hostname or "").lower() != _DOMINIO_VISMA:
        return None
    usuario, clave = cuenta
    try:
        campo_clave = pestana.locator(_SEL_CLAVE).first
        if "clave" not in escrito and campo_clave.is_visible():
            escrito.add("clave")
            campo_clave.fill(clave, timeout=_ESPERA_CAMPO_MS)
            campo_clave.press("Enter", timeout=_ESPERA_CAMPO_MS)
            return "clave"
        campo_usuario = pestana.locator(_SEL_USUARIO).first
        if "usuario" not in escrito and campo_usuario.is_visible():
            escrito.add("usuario")
            campo_usuario.fill(usuario, timeout=_ESPERA_CAMPO_MS)
            campo_usuario.press("Enter", timeout=_ESPERA_CAMPO_MS)
            return "usuario"
    except Exception as error:  # noqa: BLE001 - la pagina cambio a mitad: la persona puede seguir a mano
        if es_cierre(error):
            raise
        logger.warning("Xubio: no se pudo completar el ingreso solo (%s): sigue a mano", error)
    return None


def _mantener_viva(contexto: Any, pestana: Any) -> bool:
    """
    Pide la pagina con las cookies de la ventana (sin tocar la pestana). False
    si Xubio la mando al ingreso: la sesion ya vencio aunque la pantalla no cambio.
    """
    try:
        respuesta = contexto.request.get(pestana.url, timeout=30000)
        return not en_ingreso(respuesta.url or "")
    except Exception as error:  # noqa: BLE001
        if es_cierre(error):
            raise
        logger.info("No se pudo mantener viva la sesion de Xubio: %s", error)
        return True


def _volver_al_ingreso(pestana: Any) -> None:
    if (urlparse(pestana.url or "").hostname or "").lower() == _DOMINIO_VISMA:
        return
    try:
        pestana.goto(settings.XUBIO_URL)
    except Exception as error:  # noqa: BLE001 - queda la ventana para entrar a mano
        if es_cierre(error):
            raise
        logger.warning("Xubio: no se pudo volver al ingreso solo (%s)", error)


def _hay_captcha(pestana: Any) -> bool:
    try:
        return bool(pestana.locator(_SEL_CAPTCHA).first.is_visible())
    except Exception as error:  # noqa: BLE001
        if es_cierre(error):
            raise
        return False


def _pestana_de_xubio(contexto: Any) -> Any:
    abiertas = [p for p in contexto.pages if not p.is_closed() and "xubio" in (p.url or "").lower()]
    return abiertas[-1] if abiertas else None


class _ChromeXubio:
    """Context manager: Chrome con perfil propio, abierto en el ingreso de Xubio."""

    def __init__(self, perfil: str) -> None:
        self._perfil = Path(perfil)

    def __enter__(self) -> tuple[Any, Any]:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._perfil.mkdir(parents=True, exist_ok=True)
            self._contexto = lanzar_con_reintento(
                "xubio",
                lambda: self._playwright.chromium.launch_persistent_context(
                    str(self._perfil),
                    headless=False,  # la persona tiene que ver la ventana para entrar
                    channel=settings.CANAL_CHROME or None,
                    no_viewport=True,
                    args=ARGS_CHROME + ARGS_SIN_FRENO_EN_SEGUNDO_PLANO,
                ),
            )
            self._contexto.set_default_timeout(settings.TIMEOUT_NAVEGACION_MS)
            pestana = self._contexto.pages[0] if self._contexto.pages else self._contexto.new_page()
            pestana.goto(settings.XUBIO_URL)
            return self._contexto, pestana
        except BaseException:
            self._playwright.stop()
            raise

    def __exit__(self, *_: Any) -> None:
        try:
            self._contexto.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._playwright.stop()
            logger.info("Ventana de Xubio cerrada.")
