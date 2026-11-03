"""
El formulario de la version anterior de Mis Retenciones
(seti.afip.gob.ar/misret/consultaMisRetenciones.do?method=getForm).

Los nombres de los campos salen del HTML real capturado el 2026-11-02:

    form[name=formulario] (POST consultaMisRetenciones.do, method=lookup)
      select#cuitRetenido            CUIT del retenido (las del usuario)
      input#cuitAgente               CUIT del agente (opcional)
      input[name=impuesto]           codigo del impuesto (texto)
      select#impuestos               el mismo impuesto, en combo
      input[name=retencionPercepcion] Retencion | Percepcion | RetencionPercepcion
      input[name=fechaRetencionDesde/Hasta]  ddMMaaaa
      input[type=submit][value=Consultar]

El formulario corre su propio JavaScript (onchange de cada campo y
evaluateForm al enviar), asi que se completa con eventos reales, como una
persona, y no armando el POST a mano.

Todo esto corre en el hilo del navegador (NavegadorArca.ejecutar).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.importador_arca import leer_arca
from app.services.importador_tablas import normalizar_columnas
from app.services.impuestos import CODIGOS_ARCA, impuesto_de_texto
from app.services.tools import ErrorDeLectura

logger = logging.getLogger(__name__)

SEL_FORM = "form[name=formulario]"
SEL_CUIT_RETENIDO = "#cuitRetenido"
SEL_CUIT_AGENTE = "#cuitAgente"
SEL_IMPUESTO_TEXTO = "input[name=impuesto]"
SEL_IMPUESTOS = "#impuestos"
SEL_TIPO = "input[name=retencionPercepcion][value='{}']"
SEL_DESDE = "input[name=fechaRetencionDesde]"
SEL_HASTA = "input[name=fechaRetencionHasta]"
SEL_CONSULTAR = "input[type=submit][value=Consultar]"

TIPOS = ("Retencion", "Percepcion", "RetencionPercepcion")

# Textos de exportacion en la pagina de resultados (displaytag de Struts y
# variantes). Se prueban en orden: el CSV es el mas facil de leer.
TEXTOS_EXPORTAR = ("CSV", "Excel", "XLS")
# Paginado de la tabla de resultados.
TEXTOS_SIGUIENTE = ("Siguiente", "Próxima", "Próximo", ">")
MAX_PAGINAS = 100
# Mensajes de "no hay nada" en la pagina de resultados.
SIN_RESULTADOS = ("no se encontraron", "no existen", "no hay retenciones", "sin resultados", "nothing found")


class ErrorConsulta(RuntimeError):
    """La consulta no se pudo hacer. El mensaje es para la pantalla."""


@dataclass
class ConsultaArca:
    cuit_retenido: str
    # ARCA consulta un impuesto por vez: el bot repite el formulario con cada uno.
    impuestos: list[str]
    desde: date
    hasta: date
    tipo: str = "RetencionPercepcion"
    cuit_agente: str | None = None

    def validar(self) -> None:
        if not self.cuit_retenido:
            raise ErrorConsulta("Elegí la CUIT del retenido.")
        if not self.impuestos:
            raise ErrorConsulta("Elegí al menos un impuesto.")
        if self.tipo not in TIPOS:
            raise ErrorConsulta("Tipo de consulta inválido.")
        if self.desde > self.hasta:
            raise ErrorConsulta("La fecha desde es posterior a la fecha hasta.")


def leer_formulario(pestana: Any) -> dict:
    """Las opciones del formulario real: CUITs del retenido e impuestos."""

    def opciones(selector: str) -> list[dict]:
        return pestana.locator(selector).locator("option").evaluate_all(
            "els => els.map(o => ({valor: o.value, texto: (o.textContent || '').trim()}))"
        )

    cuits = [o["valor"] for o in opciones(SEL_CUIT_RETENIDO) if o["valor"].strip()]
    impuestos = [
        {"codigo": o["valor"], "descripcion": o["texto"]}
        for o in opciones(SEL_IMPUESTOS)
        if o["valor"].strip()
    ]
    tipos = _tipos_por_impuesto(pestana, [i["codigo"] for i in impuestos])
    for impuesto in impuestos:
        # Vacio = no se pudo saber: la pantalla ofrece los tres.
        impuesto["tipos"] = tipos.get(impuesto["codigo"], [])
    return {"cuits": cuits, "impuestos": impuestos}


# Elige cada impuesto en el combo (dispara el onchange de ARCA, que muestra u
# oculta los radios) y anota que tipos quedan a la vista. Al final deja el
# combo vacio: la consulta lo completa de nuevo.
_JS_TIPOS = """codigos => {
  const combo = document.querySelector('#impuestos');
  const radios = [...document.querySelectorAll('input[name=retencionPercepcion]')];
  const visible = r => {
    const estilo = getComputedStyle(r);
    return !r.disabled && estilo.visibility !== 'hidden' && estilo.display !== 'none';
  };
  const tipos = {};
  for (const codigo of codigos) {
    combo.value = codigo;
    combo.dispatchEvent(new Event('change', { bubbles: true }));
    tipos[codigo] = radios.filter(visible).map(r => r.value);
  }
  combo.value = '';
  combo.dispatchEvent(new Event('change', { bubbles: true }));
  return tipos;
}"""


def _tipos_por_impuesto(pestana: Any, codigos: list[str]) -> dict[str, list[str]]:
    """{codigo: tipos que ARCA admite} ("Retencion", "Percepcion", "RetencionPercepcion")."""
    try:
        tipos = pestana.evaluate(_JS_TIPOS, codigos)
    except Exception as error:  # noqa: BLE001 - sin esto la pantalla ofrece los tres
        logger.warning("No se pudieron leer los tipos de cada impuesto en ARCA: %s", error)
        return {}
    return {codigo: [t for t in TIPOS if t in valores] for codigo, valores in tipos.items()}


def impuestos_a_consultar(opciones: list[dict], detectados: set[str] | None = None) -> list[str]:
    """
    Los codigos del formulario de ARCA que hay que consultar para cubrir los
    impuestos `detectados` ("IVA", "Ganancias"...: los que tienen cuenta de
    retenciones en el Libro Mayor). Para cada impuesto se prefiere su codigo
    principal (217, 767) y, si ARCA no lo ofrece, todas las opciones cuyo
    nombre lo menciona.

    Sin detectados (o si ninguno existe en ARCA) van los principales que ofrezca
    el formulario: Ganancias e IVA, los que casi siempre tienen retenciones.
    """
    ofrecidos = [o["codigo"] for o in opciones]
    principales = [c for c in CODIGOS_ARCA if c in ofrecidos]

    elegidos: list[str] = []
    for impuesto in sorted(detectados or ()):
        propios = [c for c in principales if CODIGOS_ARCA[c] == impuesto]
        if not propios:
            propios = [
                o["codigo"] for o in opciones if impuesto_de_texto(o["codigo"], o["descripcion"]) == impuesto
            ]
        elegidos += [c for c in propios if c not in elegidos]
    return elegidos or principales


def _ddmmaaaa(fecha: date) -> str:
    return fecha.strftime("%d%m%Y")


def _completar(pestana: Any, consulta: ConsultaArca, impuesto: str) -> None:
    pestana.select_option(SEL_CUIT_RETENIDO, value=consulta.cuit_retenido)
    pestana.fill(SEL_CUIT_AGENTE, consulta.cuit_agente or "")
    # El combo dispara setRetencionPercepcion (habilita los radios que
    # corresponden al impuesto) y copia el codigo al campo de texto.
    pestana.select_option(SEL_IMPUESTOS, value=impuesto)
    pestana.wait_for_timeout(300)
    # ARCA oculta los radios que no corresponden al impuesto (con el 217 solo
    # queda "Retencion y/o Percepcion"). Si el tipo pedido esta oculto, se usa
    # el combinado, que es el unico que el formulario admite ahi.
    tipo = pestana.locator(SEL_TIPO.format(consulta.tipo))
    if not tipo.is_visible():
        logger.info("El impuesto %s no admite '%s' en ARCA: se consulta combinado", impuesto, consulta.tipo)
        tipo = pestana.locator(SEL_TIPO.format("RetencionPercepcion"))
    tipo.check()
    pestana.fill(SEL_DESDE, _ddmmaaaa(consulta.desde))
    pestana.fill(SEL_HASTA, _ddmmaaaa(consulta.hasta))


def _texto(pestana: Any) -> str:
    try:
        return pestana.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        return ""


def _exportar(pestana: Any, carpeta: Path) -> Path | None:
    """Si la pagina ofrece exportar los resultados, baja ese archivo."""
    for texto in TEXTOS_EXPORTAR:
        enlace = pestana.get_by_role("link", name=texto, exact=False).filter(visible=True).first
        try:
            if enlace.count() == 0:
                continue
            with pestana.expect_download(timeout=30000) as info:
                enlace.click()
            descarga = info.value
            ruta = carpeta / (descarga.suggested_filename or f"resultado.{texto.lower()}")
            descarga.save_as(str(ruta))
            return ruta
        except Exception as error:  # noqa: BLE001 - se prueba la siguiente forma
            logger.info("Exportar '%s' no funciono: %s", texto, error)
    return None


def _tabla_de_resultados(html: str) -> pd.DataFrame | None:
    """La tabla de la pagina que tiene fecha e importe (la de las retenciones)."""
    from io import StringIO

    try:
        tablas = pd.read_html(StringIO(html), flavor="lxml", thousands=".", decimal=",")
    except ValueError:  # ninguna tabla
        return None
    for tabla in tablas:
        if isinstance(tabla.columns, pd.MultiIndex):
            tabla.columns = [" ".join(str(p) for p in c if "Unnamed" not in str(p)) for c in tabla.columns]
        columnas = set(normalizar_columnas(tabla).columns)
        if {"FECHA", "IMPORTE"} <= columnas:
            return tabla
    return None


def _leer_tablas(pestana: Any) -> pd.DataFrame | None:
    """La tabla de resultados, recorriendo el paginado si hay."""
    paginas: list[pd.DataFrame] = []
    for _ in range(MAX_PAGINAS):
        tabla = _tabla_de_resultados(pestana.content())
        if tabla is None:
            break
        paginas.append(tabla)
        siguiente = None
        for texto in TEXTOS_SIGUIENTE:
            candidato = pestana.get_by_role("link", name=texto, exact=True).filter(visible=True).first
            if candidato.count():
                siguiente = candidato
                break
        if siguiente is None:
            break
        siguiente.click()
        pestana.wait_for_load_state("domcontentloaded")
    return pd.concat(paginas, ignore_index=True) if paginas else None


def _enviar(pestana: Any, avisos: list[str], impuesto: str, espera_s: int = 120) -> None:
    """
    Toca Consultar y espera la pagina de resultados. evaluateForm valida con
    alert() y, si algo no le gusta, no envia: en ese caso no hay navegacion y
    el texto del aviso (lo junta el manejador de dialogos) es el motivo.
    Para saber si ya cargo otra pagina se deja una marca en la actual.
    """
    avisos.clear()
    pestana.evaluate("() => { window.__botMisret = true; }")
    pestana.click(SEL_CONSULTAR, no_wait_after=True)
    for _ in range(espera_s * 4):
        if avisos:
            raise ErrorConsulta(f"ARCA rechazó la consulta del impuesto {impuesto}: {avisos[-1]}")
        try:
            if not pestana.evaluate("() => window.__botMisret === true"):
                pestana.wait_for_load_state("domcontentloaded")
                return
        except Exception:  # noqa: BLE001 - la pagina se esta reemplazando
            pass
        pestana.wait_for_timeout(250)
    raise ErrorConsulta(f"ARCA no respondió la consulta del impuesto {impuesto} a tiempo.")


def consultar_impuesto(pestana: Any, url_formulario: str, consulta: ConsultaArca, impuesto: str,
                       carpeta: Path, avisos: list[str]) -> list[dict]:
    """La consulta de un impuesto. Devuelve las retenciones ya leidas."""
    if "getForm" not in (pestana.url or ""):
        pestana.goto(url_formulario)
    pestana.wait_for_selector(SEL_FORM)
    _completar(pestana, consulta, impuesto)

    _enviar(pestana, avisos, impuesto)

    ruta = _exportar(pestana, carpeta)
    if ruta is None:
        tabla = _leer_tablas(pestana)
        if tabla is None:
            if any(t in _texto(pestana) for t in SIN_RESULTADOS):
                return []
            raise ErrorConsulta(
                f"No se encontró la tabla de resultados del impuesto {impuesto}. "
                "La página quedó guardada en datos/debug para revisarla."
            )
        ruta = carpeta / f"tabla_{impuesto}.csv"
        tabla.to_csv(ruta, sep=";", index=False, encoding="utf-8")

    try:
        return leer_arca(str(ruta))
    except ErrorDeLectura as error:
        if "ninguna retencion" in str(error).lower():
            return []
        raise ErrorConsulta(f"Impuesto {impuesto}: {error}") from error


def escribir_resultado(retenciones: list[dict], destino: Path) -> None:
    """Junta las retenciones de todas las consultas en un CSV que lee leer_arca."""
    filas = [
        {
            "CUIT Agente Ret./Perc.": r.get("cuit") or "",
            "Denominación": r.get("razon_social") or "",
            "Impuesto": r.get("impuesto") or "",
            "Régimen": r.get("regimen") or "",
            "Fecha Ret./Perc.": r["fecha"].strftime("%d/%m/%Y") if r.get("fecha") else "",
            "Número Certificado": r.get("certificado") or "",
            "Número Comprobante": r.get("comprobante") or "",
            "Importe Ret./Perc.": f"{r['importe']:.2f}".replace(".", ","),
        }
        for r in retenciones
    ]
    pd.DataFrame(filas).to_csv(destino, sep=";", index=False, encoding="utf-8")


def carpeta_temporal() -> Path:
    return Path(tempfile.mkdtemp(prefix="misret_"))
