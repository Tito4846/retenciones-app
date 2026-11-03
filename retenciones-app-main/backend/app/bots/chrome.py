"""
Utilidades de Chrome compartidas por los bots de ARCA y Xubio.

Traidas de Arca-bot (fase1_sesion.ARGS_CHROME, navegador.minimizar_ventana,
navegador._es_cierre, diagnostico.lanzar_con_reintento).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# En un contenedor /dev/shm es chico y Chrome se cae sin --disable-dev-shm-usage;
# en la PC no cambia nada.
ARGS_CHROME = ["--start-maximized", "--disable-dev-shm-usage"]

# Con la ventana minimizada Chrome frena relojes y dibujado para ahorrar
# recursos y el bot se vuelve lento: estas opciones lo apagan. No cambian nada
# de lo que ve el sitio.
ARGS_SIN_FRENO_EN_SEGUNDO_PLANO = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

ESPERA_REINTENTO_S = 3


def es_cierre(error: BaseException) -> bool:
    """Si el error de Playwright es porque Chrome (la pestana o el contexto) ya no esta."""
    texto = str(error).lower()
    return error.__class__.__name__ == "TargetClosedError" or "has been closed" in texto or "target closed" in texto


def lanzar_con_reintento(origen: str, lanzar: Callable[[], Any]) -> Any:
    """Corre el launch de Playwright; si Chrome se cae al arrancar, prueba una vez mas."""
    try:
        return lanzar()
    except Exception as error:  # noqa: BLE001
        logger.warning("Chrome (%s) no arranco, se reintenta: %s", origen, error)
    time.sleep(ESPERA_REINTENTO_S)
    return lanzar()


def minimizar_ventana(contexto: Any, pestana: Any) -> bool:
    """Minimiza la ventana de Chrome de `pestana` (por CDP). Si no se puede, sigue a la vista."""
    try:
        cdp = contexto.new_cdp_session(pestana)
        try:
            ventana = cdp.send("Browser.getWindowForTarget")
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": ventana["windowId"], "bounds": {"windowState": "minimized"}},
            )
        finally:
            cdp.detach()
    except Exception as error:  # noqa: BLE001 - es comodidad: si falla, no corta nada
        logger.info("No se pudo minimizar la ventana de Chrome (%s); queda a la vista.", error)
        return False
    return True


def mostrar_ventana(contexto: Any, pestana: Any) -> None:
    """Trae la ventana al frente (para que la persona exporte o resuelva el captcha)."""
    try:
        cdp = contexto.new_cdp_session(pestana)
        try:
            ventana = cdp.send("Browser.getWindowForTarget")
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": ventana["windowId"], "bounds": {"windowState": "maximized"}},
            )
        finally:
            cdp.detach()
        pestana.bring_to_front()
    except Exception as error:  # noqa: BLE001
        if es_cierre(error):
            raise
        logger.info("No se pudo traer la ventana al frente: %s", error)
