"""
La ventana de Chrome del bot para ARCA ("Conectar ARCA").

Adaptado de Arca-bot (fase1_sesion.py + navegador.py): el mismo login con CUIT y
clave fiscal, la misma espera del doble factor y la misma entrada al servicio
por el buscador del portal. Cambia el servicio (Mis Retenciones en vez de Mis
Facilidades) y lo que se hace despues:

  La ventana queda abierta en Mis Retenciones. La persona elige el periodo y el
  impuesto y toca "Exportar" como siempre; el bot atrapa esa descarga, la
  guarda en datos/descargas/arca y la valida con el mismo lector que usa la
  subida manual. Asi el bot no depende de los botones internos del formulario
  de consulta, que cambian sin aviso.

La clave fiscal se usa una sola vez para el login y no se guarda (ni en disco ni
en memoria): si ARCA vence la sesion, hay que volver a conectar. Para que no
venza mientras la ventana esta quieta, cada tanto se le pide la pagina a ARCA
con las cookies de la ventana (sin moverla).

Playwright (la API sync) solo se puede usar desde el hilo que abrio el
navegador: el navegador vive en un hilo propio y la API le encola pedidos.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
import unicodedata
from concurrent.futures import Future
from concurrent.futures import TimeoutError as EsperaAgotada
from dataclasses import dataclass
from datetime import datetime
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
from app.bots import misret
from app.core.config import settings
from app.services.importador_arca import leer_arca
from app.services.tools import ErrorDeLectura

logger = logging.getLogger(__name__)

DESCONECTADO = "desconectado"
CONECTANDO = "conectando"
CONECTADO = "conectado"

# Sin pedidos, cada cuanto se le pide la pagina a ARCA para que no venza.
MANTENER_VIVA_S = 4 * 60
ESPERA_CIERRE_S = 15
_VIGILANCIA_S = 1.0
# Marca del formulario de login: si aparece, ARCA vencio la sesion.
_MARCA_LOGIN = "F1:username"

MENSAJE_CERRADO = "Se cerró la ventana de ARCA: tocá «Conectar ARCA» para volver a entrar."
MENSAJE_CONECTADO = (
    "Conectado a Mis Retenciones: elegí el período y el impuesto, y tocá «Traer y conciliar» "
    "(o «Solo consultar ARCA» para traerlas sin conciliar)."
)
MENSAJE_SIN_FORMULARIO = (
    "Conectado, pero ARCA no abrió el formulario de la versión anterior de Mis Retenciones: "
    "tocá «click aquí» en la ventana de Chrome y el bot lo detecta solo."
)
# Sin formulario, cada cuanto se revisan las pestanas por si la persona llego a mano.
_BUSCAR_FORMULARIO_S = 2.0


class ErrorArca(RuntimeError):
    """Falla de login o navegacion. El mensaje es para mostrar en la pantalla."""


class ArcaDesconectado(ErrorArca):
    """No hay sesion de ARCA abierta."""


@dataclass
class DescargaArca:
    ruta: Path
    nombre: str
    filas: int
    cuando: datetime

    def a_dict(self) -> dict:
        return {"nombre": self.nombre, "filas": self.filas, "cuando": self.cuando.isoformat(timespec="seconds")}


# ----------------------------------------------------------------------
# Login y entrada al servicio (de Arca-bot fase1_sesion.py)
# ----------------------------------------------------------------------

def _login(pagina: Any, cuit: str, clave: str, reportar: Callable[[str], None]) -> None:
    """CUIT + clave fiscal en auth.afip.gob.ar, con espera del doble factor."""
    s = settings
    pagina.goto(s.ARCA_URL_LOGIN, timeout=s.TIMEOUT_LOGIN_MS)
    pagina.fill(s.ARCA_SEL_CUIT_LOGIN, cuit)
    pagina.click(s.ARCA_SEL_BTN_SIGUIENTE)
    pagina.fill(s.ARCA_SEL_CLAVE, clave)
    pagina.click(s.ARCA_SEL_BTN_INGRESAR)

    try:
        pagina.wait_for_selector(s.ARCA_SEL_BUSCADOR, timeout=s.TIMEOUT_LOGIN_MS)
    except Exception as error:
        espera_2fa = s.TIMEOUT_2FA_MS or 0
        if s.HEADLESS or espera_2fa <= 0:
            raise ErrorArca(
                "No apareció el portal después del login. Revisá la CUIT y la clave fiscal; "
                "si la cuenta tiene doble factor, el bot necesita la ventana visible (HEADLESS=false)."
            ) from error
        reportar(f"Si ARCA pide el doble factor, completalo en la ventana de Chrome (hasta {espera_2fa // 1000}s)…")
        try:
            pagina.wait_for_selector(s.ARCA_SEL_BUSCADOR, timeout=espera_2fa)
        except Exception as error_2fa:
            raise ErrorArca(
                "No se pudo entrar a ARCA: revisá la CUIT y la clave fiscal, o completá el doble factor a tiempo."
            ) from error_2fa
    pagina.wait_for_timeout(500)


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in normal if not unicodedata.combining(c)).lower()


def _sitio(url: str) -> str:
    return urlparse(url or "").netloc.lower()


def _esperar_el_servicio(pagina: Any) -> None:
    """Si el servicio no aparece en el buscador, la cuenta no esta adherida."""
    servicio = settings.ARCA_SERVICIO
    limite = time.monotonic() + settings.TIMEOUT_SERVICIO_MS / 1000
    while time.monotonic() < limite:
        try:
            if pagina.get_by_text(servicio, exact=False).count() > 0:
                return
        except Exception:  # noqa: BLE001 - todavia no aparece
            pass
        pagina.wait_for_timeout(250)
    raise ErrorArca(
        f"El servicio «{servicio}» no aparece en el portal de ARCA. Hay que adherirlo "
        "con la clave fiscal (Administrador de Relaciones) y volver a conectar."
    )


def _entrar_al_servicio(pagina: Any) -> Any:
    """
    Abre el servicio desde "Mas utilizados" o, si no esta, por el buscador del
    portal (dos flechas abajo + Enter, con clic de respaldo). Devuelve la
    pestana del servicio, que ARCA suele abrir aparte.
    """
    s = settings
    servicio = s.ARCA_SERVICIO
    sitio_portal = _sitio(pagina.url)

    acceso = pagina.get_by_text(servicio, exact=True).filter(visible=True).first
    try:
        acceso.wait_for(state="visible", timeout=s.TIMEOUT_SERVICIO_MS)
        with pagina.context.expect_page(timeout=s.TIMEOUT_NAVEGACION_MS) as info:
            acceso.click()
        return info.value
    except Exception:  # noqa: BLE001 - no esta en "mas utilizados": va por el buscador
        if _sitio(pagina.url) != sitio_portal:
            return pagina

    buscador = pagina.locator(s.ARCA_SEL_BUSCADOR)
    buscador.click(timeout=s.TIMEOUT_NAVEGACION_MS)
    # Todo menos la ultima letra de una vez y la ultima tipeada: esa tecla es la
    # que dispara las sugerencias del portal.
    buscador.fill(servicio[:-1])
    buscador.press_sequentially(servicio[-1:], delay=s.RETRASO_TIPEO_MS)
    _esperar_el_servicio(pagina)
    pagina.wait_for_timeout(800)
    pagina.keyboard.press("ArrowDown")
    pagina.keyboard.press("ArrowDown")
    pagina.wait_for_timeout(200)
    try:
        with pagina.context.expect_page(timeout=s.TIMEOUT_SERVICIO_MS) as info:
            pagina.keyboard.press("Enter")
        return info.value
    except Exception:  # noqa: BLE001
        if _sitio(pagina.url) != sitio_portal:
            return pagina

    visibles = pagina.get_by_text(servicio, exact=False).filter(visible=True)
    for indice in reversed(range(min(visibles.count(), 5))):
        try:
            with pagina.context.expect_page(timeout=s.TIMEOUT_SERVICIO_MS) as info:
                visibles.nth(indice).click()
            return info.value
        except Exception:  # noqa: BLE001
            if _sitio(pagina.url) != sitio_portal:
                return pagina
    raise ErrorArca(f"El servicio «{servicio}» aparece en el buscador pero no se pudo abrir.")


_MARCA_MISRET = "/misret/"
# El aviso "Podés acceder a la versión anterior del servicio haciendo click
# aquí": el boton es solo "click aquí" y lleva a
# .../misret/consultaMisRetenciones.do;jsessionid=...
# Playwright evalua la regex en JavaScript, donde \b no toma la "í" como letra:
# sin \b, y con la "í" compuesta o separada (i + tilde).
_TEXTO_CLICK_AQUI = re.compile(r"clic?k?\s*aqu(?:[ií]|í)", re.IGNORECASE)


def _buscar_click_aqui(pestana: Any, espera_ms: int) -> Any | None:
    """El "click aqui" visible (link o boton antes que texto suelto), en la pagina o en sus marcos."""
    limite = time.monotonic() + espera_ms / 1000
    while True:
        for marco in pestana.frames:
            candidatos = (
                marco.get_by_role("link", name=_TEXTO_CLICK_AQUI),
                marco.get_by_role("button", name=_TEXTO_CLICK_AQUI),
                marco.get_by_text(_TEXTO_CLICK_AQUI),
            )
            for candidato in candidatos:
                try:
                    boton = candidato.filter(visible=True).first
                    if boton.count() > 0:
                        return boton
                except Exception as error:  # noqa: BLE001 - el marco puede estar cargando
                    if es_cierre(error):
                        raise
        if time.monotonic() >= limite:
            return None
        pestana.wait_for_timeout(500)


def _es_formulario_misret(pestana: Any) -> bool:
    try:
        return _MARCA_MISRET in (pestana.url or "") and pestana.locator(misret.SEL_CUIT_RETENIDO).count() > 0
    except Exception as error:  # noqa: BLE001 - pestana cargando o cerrandose
        if es_cierre(error):
            raise
        return False


def _ir_a_version_anterior(pestana: Any) -> Any:
    """
    De la version nueva de Mis Retenciones (mirequa-web) a la anterior
    (seti.afip.gob.ar/misret), que es la del formulario de consulta.

    El "click aqui" del aviso es un boton con JavaScript (no un link con
    direccion), asi que se toca como lo haria una persona; puede abrir otra
    pestana o cambiar esta. Si no aparece o no lleva a ningun lado, se prueba
    ARCA_URL_MISRET en una pestana aparte: sin el pase de la version nueva
    ARCA suele contestar 401, y asi la version nueva queda intacta para que la
    persona toque "click aqui" a mano (el bot lo detecta en _atender).
    """
    if _MARCA_MISRET in (pestana.url or ""):
        return pestana
    s = settings
    boton = _buscar_click_aqui(pestana, s.TIMEOUT_NAVEGACION_MS)
    if boton is None:
        logger.info("No aparecio el 'click aqui' de la version nueva de Mis Retenciones (%s)", pestana.url)
    else:
        destino = pestana
        try:
            try:
                with pestana.context.expect_page(timeout=s.TIMEOUT_SERVICIO_MS) as info:
                    boton.click()
                destino = info.value
            except Exception:  # noqa: BLE001 - abrio en la misma pestana
                destino = pestana
            destino.wait_for_url(f"**{_MARCA_MISRET}**", timeout=s.TIMEOUT_NAVEGACION_MS)
            return destino
        except Exception as error:  # noqa: BLE001 - se prueba con la direccion directa
            if es_cierre(error):
                raise
            logger.info("El 'click aqui' no llevo a la version anterior (%s); se prueba directo", error)

    directa = pestana.context.new_page()
    try:
        respuesta = directa.goto(s.ARCA_URL_MISRET, timeout=s.TIMEOUT_NAVEGACION_MS)
        directa.wait_for_load_state("domcontentloaded")
        if (respuesta is None or respuesta.ok) and _es_formulario_misret(directa):
            return directa
        logger.info(
            "La direccion directa de Mis Retenciones no abrio el formulario (HTTP %s)",
            respuesta.status if respuesta else "?",
        )
    except Exception as error:  # noqa: BLE001
        if es_cierre(error):
            raise
        logger.info("No se pudo abrir la direccion directa de Mis Retenciones: %s", error)
    directa.close()
    pestana.bring_to_front()
    return pestana


def capturar_paginas(contexto: Any, directorio: Path, etiqueta: str) -> list[str]:
    """
    Guarda en datos/debug el HTML de cada pestana abierta (y de sus marcos, que
    los servicios viejos de ARCA usan) y una captura de pantalla. Como en
    Arca-bot: sirve para ajustar los selectores sin volver a entrar. Nunca
    corta la corrida si falla.
    """
    guardados: list[str] = []
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        sello = f"{etiqueta}_{datetime.now():%Y%m%d_%H%M%S}"
        for i, pagina in enumerate(p for p in contexto.pages if not p.is_closed()):
            for j, marco in enumerate(pagina.frames):
                destino = directorio / f"{sello}_{i}_{j}.html"
                destino.write_text(f"<!-- {marco.url} -->\n{marco.content()}", encoding="utf-8")
                guardados.append(destino.name)
            imagen = directorio / f"{sello}_{i}.png"
            pagina.screenshot(path=str(imagen), full_page=True)
            guardados.append(imagen.name)
    except Exception as error:  # noqa: BLE001 - el debug nunca hace fallar al bot
        if es_cierre(error):
            raise
        logger.warning("No se pudo guardar la captura de ARCA: %s", error)
    logger.info("Captura de ARCA guardada: %s", guardados)
    return guardados


# ----------------------------------------------------------------------
# Chrome
# ----------------------------------------------------------------------

class _ChromeArca:
    """Context manager: Chrome sin perfil (cada conexion entra de cero)."""

    def __enter__(self) -> tuple[Any, Any]:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            args = ARGS_CHROME + (ARGS_SIN_FRENO_EN_SEGUNDO_PLANO if settings.MINIMIZAR_VENTANA else [])
            self._navegador = lanzar_con_reintento(
                "arca",
                lambda: self._playwright.chromium.launch(
                    headless=settings.HEADLESS, channel=settings.CANAL_CHROME or None, args=args
                ),
            )
            self._contexto = self._navegador.new_context(no_viewport=True, accept_downloads=True)
            self._contexto.set_default_timeout(settings.TIMEOUT_NAVEGACION_MS)
            return self._contexto, self._contexto.new_page()
        except BaseException:
            self._playwright.stop()
            raise

    def __exit__(self, *_: Any) -> None:
        try:
            self._navegador.close()
        except Exception:  # noqa: BLE001 - si Chrome ya no esta, no hay nada que cerrar
            pass
        finally:
            self._playwright.stop()
            logger.info("Ventana de ARCA cerrada.")


# ----------------------------------------------------------------------
# El bot
# ----------------------------------------------------------------------

def _limpiar_cuit(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


class NavegadorArca:
    """
    Uso (desde la API):
        arca.conectar(cuit, clave)   # abre la ventana, login y Mis Retenciones
        arca.estado()                # {"estado", "mensaje", "cuit", "descarga"}
        arca.mostrar()               # trae la ventana al frente para exportar
        arca.ultima_descarga()       # el archivo exportado, o None
        arca.desconectar()
    """

    def __init__(
        self,
        lanzar: Callable[[], Any] | None = None,
        directorio: Path | None = None,
    ) -> None:
        # Los tests pasan un "lanzar" de mentira: el real abre Chrome.
        self._lanzar = lanzar or _ChromeArca
        self._directorio = directorio or settings.DIRECTORIO_DATOS / "descargas" / "arca"
        self._debug = (directorio.parent if directorio else settings.DIRECTORIO_DATOS) / "debug"
        self._candado = threading.Lock()
        self._cola: queue.Queue = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._estado = DESCONECTADO
        self._mensaje: str | None = None
        self._cuit: str | None = None
        self._descarga: DescargaArca | None = None
        self._cerrar = False
        # Opciones del formulario de Mis Retenciones (CUITs e impuestos) y su
        # direccion, leidas al entrar.
        self._formulario: dict | None = None
        self._url_formulario: str | None = None
        self._consultando = False
        # Textos de los alert() de ARCA (el formulario valida con alert).
        self._avisos: list[str] = []
        self._descargas: list[Any] = []

    # ------------------------------------------------------------------ API

    def estado(self) -> dict[str, Any]:
        """El estado para la pantalla. Nunca lleva la clave."""
        with self._candado:
            return self._estado_sin_candado()

    def ultima_descarga(self) -> DescargaArca | None:
        with self._candado:
            return self._descarga

    def conectar(self, cuit: str, clave: str) -> dict[str, Any]:
        cuit = _limpiar_cuit(cuit)
        if len(cuit) != 11 or not clave:
            raise ErrorArca("Ingresá la CUIT (11 dígitos) y la clave fiscal.")
        with self._candado:
            hilo, cerrando = self._hilo, self._cerrar
        if hilo is not None and hilo.is_alive():
            if not cerrando:
                return self.estado()
            # La ventana anterior todavia se esta cerrando: si se arrancara otra
            # ya, ese hilo leeria la cola nueva (y le robaria los pedidos) o
            # pisaria el estado de la conexion nueva al terminar.
            hilo.join(timeout=ESPERA_CIERRE_S)
            if hilo.is_alive():
                return {
                    **self.estado(),
                    "mensaje": "La ventana anterior de ARCA todavía se está cerrando: probá en unos segundos.",
                }
        with self._candado:
            self._estado, self._mensaje = CONECTANDO, "Entrando a ARCA…"
            self._cuit = cuit
            self._formulario = self._url_formulario = None
            self._cerrar = False
            self._cola = queue.Queue()
            # La clave viaja en una lista que el hilo vacia al usarla: Thread
            # guarda sus argumentos hasta terminar, y no tiene que quedar ahi.
            self._hilo = threading.Thread(target=self._bucle, args=(cuit, [clave]), name="navegador-arca", daemon=True)
            self._hilo.start()
            return self._estado_sin_candado()

    def desconectar(self) -> None:
        with self._candado:
            self._cerrar = True
            self._estado, self._mensaje = DESCONECTADO, None
        self._cola.put(None)

    def mostrar(self) -> None:
        """Trae la ventana al frente para que la persona exporte."""
        self.ejecutar(lambda pestana, contexto: mostrar_ventana(contexto, pestana))

    def capturar(self, etiqueta: str = "arca_manual") -> list[str]:
        """Guarda el HTML y una captura de cada pestana abierta (para diagnosticar)."""
        return self.ejecutar(lambda pestana, contexto: capturar_paginas(contexto, self._debug, etiqueta))

    def formulario(self) -> dict | None:
        """Las opciones del formulario de Mis Retenciones (CUITs e impuestos), o None."""
        with self._candado:
            return self._formulario

    def consultar(self, consulta: misret.ConsultaArca) -> None:
        """
        Completa el formulario de Mis Retenciones con cada impuesto, junta los
        resultados en un archivo y lo deja como la descarga de ARCA. Corre en
        segundo plano: la pantalla sigue el avance por estado().
        """
        self._reservar(consulta)
        threading.Thread(target=self._consultar, args=(consulta,), name="consulta-arca", daemon=True).start()

    def consultar_y_esperar(
        self, consulta: misret.ConsultaArca, avance: Callable[[str], None] | None = None
    ) -> DescargaArca:
        """Lo mismo que consultar() pero en el hilo que llama: para el flujo automatico."""
        self._reservar(consulta)
        try:
            return self._traer(consulta, avance)
        except (misret.ErrorConsulta, ErrorDeLectura) as error:
            self._fijar(CONECTADO, str(error))
            raise
        finally:
            with self._candado:
                self._consultando = False

    def _reservar(self, consulta: misret.ConsultaArca) -> None:
        consulta.validar()
        with self._candado:
            if self._estado != CONECTADO:
                raise ArcaDesconectado("ARCA no está conectado: tocá «Conectar ARCA».")
            if self._url_formulario is None:
                raise misret.ErrorConsulta(
                    "La ventana de ARCA no está en el formulario de Mis Retenciones (versión anterior)."
                )
            if self._consultando:
                raise misret.ErrorConsulta("Ya hay una consulta en curso.")
            self._consultando = True

    def _consultar(self, consulta: misret.ConsultaArca) -> None:
        try:
            self._traer(consulta)
        except (misret.ErrorConsulta, ErrorDeLectura) as error:
            self._fijar(CONECTADO, str(error))
        except ArcaDesconectado:
            pass  # el estado ya lo dice
        except Exception as error:  # noqa: BLE001
            logger.exception("Fallo la consulta de Mis Retenciones")
            self._fijar(CONECTADO, f"No se pudo consultar Mis Retenciones: {error}")
        finally:
            with self._candado:
                self._consultando = False

    def _traer(self, consulta: misret.ConsultaArca, avance: Callable[[str], None] | None = None) -> DescargaArca:
        """Consulta cada impuesto y deja el resultado combinado como la descarga de ARCA."""
        retenciones = self.ejecutar(
            lambda pestana, contexto: self._consultar_en_ventana(pestana, contexto, consulta, avance)
        )
        nombre = (
            f"Mis Retenciones {consulta.cuit_retenido} {'+'.join(consulta.impuestos)} "
            f"{consulta.desde:%d%m%Y}-{consulta.hasta:%d%m%Y}.csv"
        )
        self._directorio.mkdir(parents=True, exist_ok=True)
        ruta = self._directorio / f"{datetime.now():%Y%m%d_%H%M%S}_{nombre}"
        misret.escribir_resultado(retenciones, ruta)
        descarga = DescargaArca(ruta=ruta, nombre=nombre, filas=len(retenciones), cuando=datetime.now())
        with self._candado:
            anterior = self._descarga
            self._descarga = descarga
        if anterior and anterior.ruta != ruta:
            anterior.ruta.unlink(missing_ok=True)
        self._fijar(CONECTADO, f"ARCA devolvió {len(retenciones)} retenciones. Ya podés conciliar.")
        return descarga

    def _consultar_en_ventana(
        self,
        pestana: Any,
        contexto: Any,
        consulta: misret.ConsultaArca,
        avance: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """En el hilo del navegador: una consulta por impuesto, con captura de cada resultado."""
        carpeta = misret.carpeta_temporal()
        retenciones: list[dict] = []
        try:
            for numero, impuesto in enumerate(consulta.impuestos, start=1):
                mensaje = f"Consultando el impuesto {impuesto} en ARCA ({numero} de {len(consulta.impuestos)})…"
                self._fijar(CONECTADO, mensaje)
                if avance:
                    avance(mensaje)
                try:
                    retenciones += misret.consultar_impuesto(
                        pestana, self._url_formulario, consulta, impuesto, carpeta, self._avisos
                    )
                finally:
                    capturar_paginas(contexto, self._debug, f"arca_resultado_{impuesto}")
            return retenciones
        finally:
            # Las exportaciones de la consulta tambien pasan por el oyente de
            # descargas: no son "la descarga" de la persona.
            self._descargas.clear()
            for archivo in carpeta.iterdir():
                archivo.unlink(missing_ok=True)
            carpeta.rmdir()

    def ejecutar(self, fn: Callable[[Any, Any], Any]) -> Any:
        with self._candado:
            if self._estado != CONECTADO or self._hilo is None or not self._hilo.is_alive():
                raise ArcaDesconectado("ARCA no está conectado: tocá «Conectar ARCA».")
            hilo = self._hilo
        futuro: Future = Future()
        self._cola.put((fn, futuro))
        while True:
            try:
                return futuro.result(timeout=_VIGILANCIA_S)
            except EsperaAgotada:
                if not hilo.is_alive() and not futuro.done():
                    raise ArcaDesconectado(MENSAJE_CERRADO) from None

    # ----------------------------------------------------- hilo del navegador

    def _estado_sin_candado(self) -> dict[str, Any]:
        return {
            "estado": self._estado,
            "mensaje": self._mensaje,
            "cuit": self._cuit,
            "descarga": self._descarga.a_dict() if self._descarga else None,
            "formulario": self._formulario,
            "consultando": self._consultando,
        }

    def _fijar(self, estado: str, mensaje: str | None) -> None:
        with self._candado:
            if self._cerrar:
                return  # se desconecto a pedido: lo que vea el hilo mientras cierra ya no cuenta
            self._estado, self._mensaje = estado, mensaje
        logger.info("ARCA: %s%s", estado, f" ({mensaje})" if mensaje else "")

    def _bucle(self, cuit: str, sobre_clave: list[str]) -> None:
        try:
            with self._lanzar() as (contexto, pagina):
                descargas = self._descargas
                descargas.clear()

                # Funciones propias y no descargas.append: Playwright le guarda
                # un atributo al manejador, y un metodo built-in no lo admite.
                def al_descargar(descarga: Any) -> None:
                    descargas.append(descarga)

                def al_dialogo(dialogo: Any) -> None:
                    # Mis Retenciones valida con alert(): se anota y se cierra.
                    self._avisos.append(dialogo.message)
                    dialogo.dismiss()

                def escuchar(pestana_nueva: Any) -> None:
                    pestana_nueva.on("download", al_descargar)
                    pestana_nueva.on("dialog", al_dialogo)

                contexto.on("page", escuchar)
                escuchar(pagina)

                _login(pagina, cuit, sobre_clave.pop(), lambda m: self._fijar(CONECTANDO, m))
                self._fijar(CONECTANDO, f"Abriendo «{settings.ARCA_SERVICIO}»…")
                pestana = _entrar_al_servicio(pagina)
                pestana.wait_for_load_state("domcontentloaded")
                if settings.ARCA_VERSION_ANTERIOR:
                    self._fijar(CONECTANDO, "Pasando a la versión anterior de Mis Retenciones…")
                    pestana = _ir_a_version_anterior(pestana)
                    pestana.wait_for_load_state("domcontentloaded")
                try:
                    pestana.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001 - ARCA a veces deja pedidos abiertos
                    pass
                capturar_paginas(contexto, self._debug, "arca_servicio")
                self._leer_formulario(pestana)
                self._fijar(CONECTADO, MENSAJE_CONECTADO if self._formulario else MENSAJE_SIN_FORMULARIO)
                self._atender(contexto, pestana, descargas)
        except BaseException as error:  # noqa: BLE001 - Chrome o ARCA pueden fallar de mil formas
            sobre_clave.clear()
            if isinstance(error, ErrorArca):
                self._fijar(DESCONECTADO, str(error))
            elif es_cierre(error):
                self._fijar(DESCONECTADO, MENSAJE_CERRADO)
            else:
                logger.exception("El bot de ARCA fallo")
                self._fijar(DESCONECTADO, f"No se pudo conectar con ARCA: {error}")
        finally:
            self._vaciar_cola()

    def _leer_formulario(self, pestana: Any) -> None:
        try:
            formulario = misret.leer_formulario(pestana)
        except Exception as error:  # noqa: BLE001 - sin formulario queda la via manual
            if es_cierre(error):
                raise
            logger.warning("No se pudo leer el formulario de Mis Retenciones: %s", error)
            return
        if formulario["cuits"]:
            with self._candado:
                self._formulario = formulario
                self._url_formulario = pestana.url

    def _atender(self, contexto: Any, pestana: Any, descargas: list[Any]) -> None:
        # Sin formulario la ventana queda a la vista: la persona tiene que tocar
        # "click aqui" para llegar a la version anterior.
        buscar_formulario = self.formulario() is None
        if settings.MINIMIZAR_VENTANA and not settings.HEADLESS and not buscar_formulario:
            minimizar_ventana(contexto, pestana)
        ultimo_uso = time.monotonic()
        proxima_busqueda = time.monotonic()
        while True:
            if not any(not p.is_closed() for p in contexto.pages):
                self._fijar(DESCONECTADO, MENSAJE_CERRADO)
                return
            while descargas:
                self._guardar_descarga(descargas.pop(0))
            if buscar_formulario and time.monotonic() >= proxima_busqueda:
                proxima_busqueda = time.monotonic() + _BUSCAR_FORMULARIO_S
                encontrada = self._buscar_formulario(contexto)
                if encontrada is not None:
                    pestana, buscar_formulario = encontrada, False
                    self._fijar(CONECTADO, MENSAJE_CONECTADO)
            if time.monotonic() - ultimo_uso > MANTENER_VIVA_S:
                ultimo_uso = time.monotonic()
                if not self._mantener_viva(contexto, pestana):
                    self._fijar(DESCONECTADO, "ARCA venció la sesión: volvé a conectar.")
                    return
            try:
                item = self._cola.get(timeout=_VIGILANCIA_S)
            except queue.Empty:
                continue
            if item is None:
                return
            fn, futuro = item
            ultimo_uso = time.monotonic()
            if futuro.set_running_or_notify_cancel():
                try:
                    futuro.set_result(fn(pestana, contexto))
                except BaseException as error:  # noqa: BLE001 - viaja al hilo que pidio
                    futuro.set_exception(error)
                    if es_cierre(error):
                        raise

    def _buscar_formulario(self, contexto: Any) -> Any | None:
        """La pestana con el formulario de Mis Retenciones (si la persona llego a mano), ya leido."""
        for candidata in [p for p in contexto.pages if not p.is_closed()]:
            if not _es_formulario_misret(candidata):
                continue
            self._leer_formulario(candidata)
            if self.formulario() is not None:
                logger.info("Formulario de Mis Retenciones encontrado en %s", candidata.url)
                return candidata
        return None

    def _guardar_descarga(self, descarga: Any) -> None:
        """Guarda lo que la persona exporto y lo valida con el lector de ARCA."""
        nombre = descarga.suggested_filename or "retenciones.csv"
        self._directorio.mkdir(parents=True, exist_ok=True)
        ruta = self._directorio / f"{datetime.now():%Y%m%d_%H%M%S}_{nombre}"
        try:
            descarga.save_as(str(ruta))
            filas = len(leer_arca(str(ruta)))
        except ErrorDeLectura as error:
            self._fijar(CONECTADO, f"Se descargó «{nombre}» pero no se pudo leer: {error}")
            return
        except Exception as error:  # noqa: BLE001
            if es_cierre(error):
                raise
            logger.exception("No se pudo guardar la descarga de ARCA")
            self._fijar(CONECTADO, f"No se pudo guardar la descarga «{nombre}»: {error}")
            return

        with self._candado:
            anterior = self._descarga
            self._descarga = DescargaArca(ruta=ruta, nombre=nombre, filas=filas, cuando=datetime.now())
        if anterior and anterior.ruta != ruta:
            anterior.ruta.unlink(missing_ok=True)
        self._fijar(CONECTADO, f"Archivo de ARCA recibido: {filas} retenciones. Ya podés conciliar.")

    def _mantener_viva(self, contexto: Any, pestana: Any) -> bool:
        """Pide la pagina con las cookies de la ventana; False si ARCA devolvio el login."""
        try:
            respuesta = contexto.request.get(pestana.url, timeout=30000)
            return _MARCA_LOGIN not in respuesta.text()
        except Exception as error:  # noqa: BLE001
            if es_cierre(error):
                raise
            logger.info("No se pudo mantener viva la sesion de ARCA: %s", error)
            return True

    def _vaciar_cola(self) -> None:
        while True:
            try:
                item = self._cola.get_nowait()
            except queue.Empty:
                return
            if item is not None and not item[1].done():
                item[1].set_exception(ArcaDesconectado(MENSAJE_CERRADO))
