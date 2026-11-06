"""
El recorrido del bot por la pantalla de Libro Mayor de Xubio.

Lo mismo que hace una persona:

1. Entra a Contabilidad -> Libro Mayor de la empresa con la que se entro a
   Xubio en Conexiones (la que muestra arriba, junto a «Estudio:»).
2. En «Elige los filtros» pone Desde y Hasta (las fechas de la consulta de ARCA).
3. En «Cuenta» escribe el comienzo del nombre ("Retención"), mira las cuentas
   que ofrece Xubio y elige la del impuesto y el tipo de ARCA (ver
   cuentas_xubio). «Cuenta» abre una lista con casillas y un buscador propio:
   con "Retencion y/o Percepcion" tilda las dos ("Retención Ganancias
   Sufrida" y "Percepción de Ganancias Sufrida") y toca el «Aceptar» de la lista.
4. Toca «Aceptar» una vez y se queda con lo que Xubio carga en la pantalla: la
   misma respuesta del reporte que dibuja la grilla.
Si algo de la pantalla no aparece, se guarda una captura en datos/debug y
sale ErrorPantallaXubio con lo que falto.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import urlparse

from app.core.config import settings
from app.services.cuentas_xubio import ETIQUETA_TIPO, elegir_cuenta, terminos_de_busqueda, tipos_simples

logger = logging.getLogger(__name__)

TITULO_FILTROS = "Elige los filtros"
_ESPERA_MS = 15000
_ESPERA_OPCIONES_MS = 2500
_ESPERA_REPORTE_MS = 90000
_RUTA_REPORTE = "webreport/data"
# Contabilidad -> Libro Mayor (el enlace del menu de Xubio).
RUTA_LIBRO_MAYOR = "/NXV/contabilidad/libro-mayor-ar"
_ESPERA_NAVEGACION_MS = 60000

# La empresa activa: la que muestra Xubio arriba, junto a «Estudio:».
_JS_EMPRESA = "() => (document.querySelector('.nombre-tenant')?.textContent || '').trim() || null"

Avance = Callable[[str], None]


class ErrorPantallaXubio(RuntimeError):
    """La pantalla de Libro Mayor no respondio como se esperaba. El mensaje es para la pantalla."""


def _base(pestana: Any) -> str:
    if settings.XUBIO_WEB_URL:
        return settings.XUBIO_WEB_URL.rstrip("/")
    partes = urlparse(pestana.url)
    return f"{partes.scheme}://{partes.netloc}"


def empresa_activa(pestana: Any) -> str | None:
    """El nombre de la empresa con la que se entro a Xubio, o None si la pagina no lo muestra."""
    try:
        return pestana.evaluate(_JS_EMPRESA)
    except Exception as error:  # noqa: BLE001 - una pagina sin el encabezado (ingreso, recargando)
        logger.info("Xubio: no se pudo leer la empresa activa: %s", error)
        return None


def _filtros(pestana: Any) -> Any:
    """El dialogo «Elige los filtros»: el contenedor del titulo que tiene el boton Aceptar."""
    return pestana.locator(
        f"xpath=//*[normalize-space(text())='{TITULO_FILTROS}']"
        "/ancestor::*[.//*[normalize-space(text())='Aceptar']][1]"
    ).first


def _campo(dialogo: Any, etiqueta: str) -> Any:
    """El campo a la derecha de una etiqueta del dialogo (Desde, Hasta, Cuenta...)."""
    return dialogo.locator(f"xpath=.//*[normalize-space(text())='{etiqueta}']/following::input[1]").first


def _visible(locator: Any, espera_ms: int) -> bool:
    try:
        locator.wait_for(state="visible", timeout=espera_ms)
        return True
    except Exception:  # noqa: BLE001 - se prueba otro camino
        return False


def _filtros_visibles(pestana: Any, espera_ms: int) -> tuple[Any, Any] | None:
    """(marco, dialogo) de «Elige los filtros»: puede estar en la pagina o en un iframe."""
    limite = espera_ms
    while True:
        for marco in pestana.frames:
            dialogo = _filtros(marco)
            try:
                if dialogo.is_visible():
                    return marco, dialogo
            except Exception:  # noqa: BLE001 - el marco se fue mientras se miraba
                continue
        if limite <= 0:
            return None
        pestana.wait_for_timeout(300)
        limite -= 300


def abrir_filtros(pestana: Any) -> tuple[Any, Any]:
    """Entra a Libro Mayor y devuelve (marco, dialogo) de «Elige los filtros»."""
    encontrado = _filtros_visibles(pestana, 0)
    if encontrado:
        return encontrado

    pestana.goto(settings.XUBIO_URL_LIBRO_MAYOR or _base(pestana) + RUTA_LIBRO_MAYOR, timeout=_ESPERA_NAVEGACION_MS)
    encontrado = _filtros_visibles(pestana, _ESPERA_MS)
    if encontrado:
        return encontrado
    # El dialogo no se abrio solo: el boton de filtros de la pantalla.
    for marco in pestana.frames:
        boton = marco.get_by_role("button", name=re.compile("filtr", re.IGNORECASE)).first
        if _visible(boton, 1000):
            boton.click()
            encontrado = _filtros_visibles(pestana, _ESPERA_MS)
            if encontrado:
                return encontrado
    raise ErrorPantallaXubio("Entré a Libro Mayor pero no apareció «Elige los filtros».")


def _poner_fecha(campo: Any, dia: date) -> None:
    """
    Xubio no tiene un campo de fecha: son tres casilleros (dia, mes y año, con
    maxlength) dentro de .widgetDate. Se completan directo y no con el teclado:
    dia y mes tienen un onkeydown propio que bloquea Ctrl+A y salta solo al
    siguiente casillero, y lo que se teclea se pierde. Al aceptar, FAFWidgetDate
    lee el valor de cada casillero (getValue), asi que alcanza con dejarlo ahi.
    Se relee: si no quedo lo pedido hay que enterarse aca, no en el cruce.
    """
    caja = campo.locator("xpath=ancestor::div[contains(concat(' ', @class, ' '), ' widgetDate ')][1]")
    if not caja.count():
        raise ErrorPantallaXubio("No encontré los casilleros de fecha de «Elige los filtros».")
    partes = (("day", dia.day), ("month", dia.month), ("year", dia.year))
    for nombre, valor in partes:
        casillero = caja.locator(f"input[name={nombre}]")
        casillero.fill(f"{valor:02d}", timeout=_ESPERA_MS)
    casillero.blur()  # Xubio valida la fecha al salir
    quedo = tuple(int(caja.locator(f"input[name={nombre}]").input_value() or 0) for nombre, _ in partes)
    if quedo != (dia.day, dia.month, dia.year):
        raise ErrorPantallaXubio(f"Xubio no tomó la fecha {dia:%d/%m/%Y}.")


# La lista que abre «Cuenta»: casillas por cuenta, un buscador propio (queda
# encima del campo) y un «Aceptar» abajo que confirma las tildadas.
_LISTA = ".fafselector"

_JS_OPCIONES = """
(termino) => {
  // Lo que Xubio lista al escribir en «Cuenta»: elementos visibles, sin hijos
  // con texto propio, que mencionan lo escrito (sin importar acentos ni
  // mayusculas). Se marcan para poder tocarlos. Con la lista abierta, solo
  // se mira adentro de ella.
  const llano = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const t = llano(termino);
  document.querySelectorAll('[data-bot-opcion]').forEach((e) => e.removeAttribute('data-bot-opcion'));
  const lista = [...document.querySelectorAll('%LISTA%')].find((e) => e.getBoundingClientRect().height);
  const vistos = [];
  for (const e of (lista || document.body).querySelectorAll('*')) {
    if (e.closest('input, textarea, script, style')) continue;
    const texto = (e.innerText || '').trim();
    if (!texto || texto.length > 160 || !llano(texto).includes(t)) continue;
    if ([...e.children].some((h) => (h.innerText || '').trim() === texto)) continue;
    const caja = e.getBoundingClientRect();
    if (!caja.width || !caja.height || getComputedStyle(e).visibility === 'hidden') continue;
    e.setAttribute('data-bot-opcion', String(vistos.length));
    vistos.push(texto);
  }
  return vistos;
}
""".replace("%LISTA%", _LISTA)


def _lista_abierta(marco: Any) -> Any | None:
    """La lista de cuentas de «Cuenta», si esta abierta."""
    lista = marco.locator(_LISTA).last
    try:
        return lista if lista.count() and lista.is_visible() else None
    except Exception:  # noqa: BLE001 - se cerro mientras se miraba
        return None


def _opciones_de(pestana: Any, marco: Any, campo: Any, termino: str) -> list[str]:
    """
    Escribe `termino` y devuelve lo que Xubio lista. La primera vez se escribe
    en «Cuenta», que abre la lista; despues, en el buscador de la lista, que
    queda encima del campo (y lo tapa).
    """
    lista = _lista_abierta(marco)
    buscador = lista.locator(".searchBox input").first if lista is not None else campo
    if lista is not None and not buscador.count():
        buscador = lista.locator("input:not([type=checkbox])").first
    buscador.click()
    buscador.fill("")
    buscador.type(termino, delay=60)
    pestana.wait_for_timeout(_ESPERA_OPCIONES_MS)
    opciones: list[str] = marco.evaluate(_JS_OPCIONES, termino)
    if not opciones:
        buscador.press("Enter" if lista is not None else "ArrowDown")
        pestana.wait_for_timeout(_ESPERA_OPCIONES_MS)
        opciones = marco.evaluate(_JS_OPCIONES, termino)
    return opciones


def _tildar(marco: Any, indice: int) -> None:
    """Toca la cuenta listada y, si tiene casilla, se asegura de que quede tildada."""
    opcion = marco.locator(f"[data-bot-opcion='{indice}']").first
    opcion.click()
    casilla = opcion.locator("xpath=ancestor-or-self::tr[1]//input[@type='checkbox']").first
    if casilla.count() and not casilla.is_checked():
        casilla.check()


def _elegir_en_cuenta(pestana: Any, marco: Any, campo: Any, impuesto: str, tipo: str) -> str:
    """
    Escribe en «Cuenta» el comienzo del nombre ("Retención", "Percepción"),
    tilda la cuenta sufrida del impuesto y devuelve su nombre.
    """
    vistas: list[str] = []
    for termino in terminos_de_busqueda(impuesto, tipo):
        opciones = _opciones_de(pestana, marco, campo, termino)
        elegida = elegir_cuenta(opciones, impuesto, tipo)
        logger.info("Xubio, «Cuenta» con %r ofrece %s -> %s", termino, opciones, elegida)
        if elegida is not None:
            break
        vistas += [o for o in opciones if o not in vistas]
    else:
        raise ErrorPantallaXubio(
            f"En «Cuenta» de Xubio no hay una cuenta de {ETIQUETA_TIPO[tipo]} de {impuesto} sufrida "
            f"(aparecen: {', '.join(vistas) or 'ninguna'})."
        )
    _tildar(marco, opciones.index(elegida))
    return elegida


def _confirmar_lista(pestana: Any, marco: Any) -> None:
    """Toca el «Aceptar» de la lista de cuentas para que queden las tildadas."""
    lista = _lista_abierta(marco)
    if lista is None:
        return
    lista.get_by_text("Aceptar", exact=True).last.click()
    try:
        lista.wait_for(state="hidden", timeout=_ESPERA_MS)
    except Exception as error:  # noqa: BLE001
        raise ErrorPantallaXubio("La lista de cuentas de «Cuenta» no se cerró al tocar su «Aceptar».") from error


def _es_reporte(respuesta: Any) -> bool:
    """La respuesta con la que Xubio dibuja la grilla del Libro Mayor (no sus scripts ni estilos)."""
    url = respuesta.url.lower().split("?")[0]
    if _RUTA_REPORTE in url:
        return True
    return "webreport" in url and respuesta.request.method == "POST" and not url.endswith((".js", ".css"))


def _guardar_respuesta(texto: str) -> None:
    """Lo que devolvio Xubio, en datos/debug, para ver que trajo si no se puede leer."""
    try:
        carpeta = settings.DIRECTORIO_DATOS / "debug"
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"xubio_libro_mayor_respuesta_{datetime.now():%Y%m%d_%H%M%S}.txt").write_text(texto, encoding="utf-8")
    except Exception as error:  # noqa: BLE001 - es solo para diagnostico
        logger.info("Xubio: no se pudo guardar la respuesta del Libro Mayor: %s", error)


def _aceptar(pestana: Any, dialogo: Any) -> str:
    """Toca Aceptar y devuelve lo que Xubio carga en la grilla."""
    boton = dialogo.locator(".btnFilterAcep").first
    if not boton.count():
        boton = dialogo.get_by_text("Aceptar", exact=True).first
    pedidos: list[str] = []

    def anotar(pedido: Any) -> None:
        if pedido.method == "POST":
            pedidos.append(pedido.url.split("?")[0])

    pestana.on("request", anotar)
    try:
        with pestana.expect_response(_es_reporte, timeout=_ESPERA_REPORTE_MS) as respuesta:
            boton.click()
        texto = respuesta.value.text()
    except Exception as error:  # noqa: BLE001 - Xubio no pidio el reporte o tardo demasiado
        if "Timeout" not in type(error).__name__ and "Timeout" not in str(error):
            raise
        vistos = ", ".join(dict.fromkeys(pedidos)) or "ninguno"
        raise ErrorPantallaXubio(
            f"Toqué «Aceptar» pero Xubio no cargó el Libro Mayor en {_ESPERA_REPORTE_MS // 1000} s "
            f"(pedidos de la página: {vistos})."
        ) from error
    finally:
        pestana.remove_listener("request", anotar)
    _guardar_respuesta(texto)
    logger.info("Xubio: Libro Mayor desde %s (%d caracteres)", respuesta.value.url, len(texto))
    if not respuesta.value.ok:
        raise ErrorPantallaXubio(f"Xubio no cargó el Libro Mayor (HTTP {respuesta.value.status}).")
    return texto


def recorrer(
    pestana: Any,
    desde: date,
    hasta: date,
    impuesto: str,
    tipo: str,
    avance: Avance = lambda detalle: None,
) -> list[tuple[str, str]]:
    """
    [(cuentas, reporte)] del Libro Mayor de las cuentas que corresponden a ARCA,
    todas elegidas en el mismo filtro («cuentas»: sus nombres separados por " / ").
    """
    avance(f"Xubio: Libro Mayor, filtros del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}…")
    marco, dialogo = abrir_filtros(pestana)
    _poner_fecha(_campo(dialogo, "Desde"), desde)
    _poner_fecha(_campo(dialogo, "Hasta"), hasta)
    cuentas: list[str] = []
    for tipo_simple in tipos_simples(tipo):
        cuenta = _elegir_en_cuenta(pestana, marco, _campo(dialogo, "Cuenta"), impuesto, tipo_simple)
        if cuenta not in cuentas:
            cuentas.append(cuenta)
    _confirmar_lista(pestana, marco)
    avance(f"Xubio: Libro Mayor de «{'» y «'.join(cuentas)}»…")
    return [(" / ".join(cuentas), _aceptar(pestana, dialogo))]
