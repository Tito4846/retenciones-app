"""
El recorrido del bot por Libro Mayor contra una copia local de la pantalla de
Xubio («Elige los filtros» con Desde, Hasta y Cuenta, que sugiere cuentas al
escribir; Aceptar pide el reporte y lo dibuja), manejada con el Chrome real.
Se saltea si no hay Chrome instalado.
"""

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.bots import libro_mayor_xubio
from app.core.config import settings
from app.services.importador_mayor import leer_mayor_desde_webreport

# Los nombres del plan de cuentas real (Libro Mayor de un cliente).
PLAN = [
    "Retención Ganancias Sufrida",
    "Percepción de Ganancias Sufrida",
    "Retención IVA Sufrida",
    "Retenciones de Ganancias a depositar",
    "Ganancias por diferencia de cambio",
    "Retención Ganancias",
    "Retención Ganancias Efectuada",
    "Percepción de Ganancias Efectuada",
]

PANTALLA = """<html><body>
<div class="compania"><span class="estudio-label">Estudio:</span><span class="nombre-tenant">%ACTIVA%</span>
  <ul id="compania-lista">
    <li class="licomp"><a href="/NXV/Relogin?tenant=tnt_1">PICCININI y ASOCIADOS S.A.</a></li>
    <li class="licomp"><a href="/NXV/Relogin?tenant=tnt_2">MATERIALES SAN FERNANDO S.A.</a></li>
  </ul>
</div>
<div id="filtros">
  <h2>Elige los filtros</h2>
  <table>
    <tr><td>Desde</td><td><input type="date" id="desde" value="2026-01-01"></td>
        <td>Hasta</td><td><input type="date" id="hasta" value="2026-11-03"></td></tr>
    <tr><td>Cuenta</td><td><input type="text" id="cuenta" autocomplete="off"></td>
        <td>Organización</td><td><input type="text"></td></tr>
  </table>
  <button id="cancelar">Cancelar</button><button id="aceptar">Aceptar</button>
</div>
<!-- Como Xubio: «Cuenta» abre una lista con casillas, con su propio buscador
     encima del campo (lo tapa) y un «Aceptar» abajo. -->
<div class="fafselector panel" style="display:none; position:absolute; background:#fff; z-index:5">
  <div class="searchBox"><input class="WidgetTextBox"></div>
  <table><tbody id="filas"></tbody></table>
  <a id="botonNuevo" style="cursor:pointer">Aceptar</a>
</div>
<div id="grilla"></div>
<script>
  const PLAN = %PLAN%;
  const cuenta = document.querySelector('#cuenta');
  const panel = document.querySelector('.fafselector');
  const buscador = panel.querySelector('.searchBox input');
  const filas = document.querySelector('#filas');
  let cuentas = [];
  PLAN.forEach((c) => {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><input type="checkbox"><div class="caption"></div></td>';
    tr.querySelector('.caption').textContent = c;
    // Como Xubio: tocar la fila tilda o destilda la casilla.
    tr.onclick = (e) => { if (e.target.type !== 'checkbox') { const x = tr.querySelector('input'); x.checked = !x.checked; } };
    filas.appendChild(tr);
  });
  // Como Xubio: filtra por el comienzo del nombre ("Ganancia" no trae
  // "Retención Ganancias Sufrida"), un rato despues de escribir.
  const filtrar = (texto) => setTimeout(() => {
    const t = texto.toLowerCase();
    for (const tr of filas.children) tr.style.display = tr.textContent.toLowerCase().startsWith(t) ? '' : 'none';
  }, 300);
  cuenta.addEventListener('input', () => {
    const caja = cuenta.getBoundingClientRect();
    Object.assign(panel.style, {display: 'block', left: caja.left + 'px', top: (caja.top + scrollY - 2) + 'px'});
    buscador.value = cuenta.value;
    buscador.focus();
    filtrar(cuenta.value);
  });
  buscador.addEventListener('input', () => filtrar(buscador.value));
  document.querySelector('#botonNuevo').onclick = () => {
    cuentas = [...filas.children].filter((tr) => tr.querySelector('input').checked).map((tr) => tr.textContent);
    panel.style.display = 'none';
  };
  document.querySelector('#aceptar').onclick = async () => {
    const pedido = {desde: document.querySelector('#desde').value,
                    hasta: document.querySelector('#hasta').value, cuentas};
    const r = await fetch('/NXV/webreport/data?layout=LibroMayor', {method: 'POST', body: JSON.stringify(pedido)});
    document.querySelector('#filtros').style.display = 'none';
    document.querySelector('#grilla').textContent = await r.text();
  };
</script></body></html>"""


def _reporte(cuentas: list[str]) -> str:
    filas = {
        PLAN[0]: "1,2026-10-11,16100.00,0,Cobro factura 33,Perez Juan",
        PLAN[1]: "2,2026-10-20,1500.00,0,Percepcion compra,Proveedor SRL",
    }
    datos = ";".join(filas.get(c, "") for c in cuentas)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<root><dataset>
<fields><field name="FECHA"/><field name="DEBE"/><field name="HABER"/><field name="DESCRIPCION"/><field name="ORGANIZACION"/></fields>
<data>{datos}</data>
</dataset></root>"""


class _Xubio(BaseHTTPRequestHandler):
    pedidos: list[dict] = []
    rutas: list[str] = []
    empresas = {"tnt_1": "PICCININI y ASOCIADOS S.A.", "tnt_2": "MATERIALES SAN FERNANDO S.A."}
    activa = "tnt_1"

    def _responder(self, cuerpo: str, tipo: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.end_headers()
        self.wfile.write(cuerpo.encode("utf-8"))

    def do_GET(self):  # noqa: N802
        _Xubio.rutas.append(self.path)
        if self.path.startswith("/NXV/Relogin?tenant="):
            _Xubio.activa = self.path.split("=", 1)[1]
        pagina = PANTALLA.replace("%PLAN%", json.dumps(PLAN)).replace("%ACTIVA%", _Xubio.empresas[_Xubio.activa])
        if not self.path.startswith("/NXV/contabilidad/libro-mayor-ar"):
            # Inicio y el resto de las pantallas: sin el dialogo de filtros.
            pagina = pagina.replace('<div id="filtros">', '<div id="filtros" style="display:none">')
        self._responder(pagina, "text/html")

    def do_POST(self):  # noqa: N802
        pedido = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        _Xubio.pedidos.append(pedido)
        self._responder(_reporte(pedido["cuentas"]), "application/xml")

    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def _debug_aparte(tmp_path, monkeypatch):
    # Lo que el bot guarda para diagnostico no va a la carpeta de datos real.
    monkeypatch.setattr(settings, "DIRECTORIO_DATOS", tmp_path)


@pytest.fixture(scope="module")
def pantalla():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Xubio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def pestana():
    playwright = pytest.importorskip("playwright.sync_api").sync_playwright().start()
    try:
        navegador = playwright.chromium.launch(headless=True, channel="chrome")
    except Exception as error:  # noqa: BLE001
        playwright.stop()
        pytest.skip(f"No hay Chrome para la prueba: {error}")
    yield navegador.new_page()
    navegador.close()
    playwright.stop()


def test_recorre_libro_mayor_con_las_fechas_y_las_cuentas_de_arca(pestana, pantalla, monkeypatch):
    monkeypatch.setattr(settings, "XUBIO_WEB_URL", pantalla)
    _Xubio.pedidos.clear()
    avances = []

    leidos = libro_mayor_xubio.recorrer(
        pestana, date(2026, 10, 1), date(2026, 11, 5), "Ganancias", "RetencionPercepcion", avances.append
    )

    # Las dos cuentas en el mismo filtro y un solo Aceptar: «Retención Ganancias
    # Sufrida» y «Percepción de Ganancias Sufrida», nunca la de "a depositar"
    # (lo que retiene la empresa).
    assert _Xubio.pedidos == [{"desde": "2026-10-01", "hasta": "2026-11-05", "cuentas": PLAN[:2]}]
    [(cuentas, reporte)] = leidos
    assert cuentas == f"{PLAN[0]} / {PLAN[1]}"
    # El reporte viene sin la columna Cuenta: se completa con las elegidas.
    mayor = leer_mayor_desde_webreport(reporte, vacio_ok=True, cuenta=cuentas)
    assert [(m["importe"], m["impuesto"]) for m in mayor.retenciones] == [(16100.0, "Ganancias"), (1500.0, "Ganancias")]
    assert any("«Retención Ganancias Sufrida» y «Percepción de Ganancias Sufrida»" in a for a in avances)


def test_sin_la_cuenta_en_el_plan_avisa_que_cuentas_hay(pestana, pantalla, monkeypatch):
    monkeypatch.setattr(settings, "XUBIO_WEB_URL", pantalla)
    with pytest.raises(libro_mayor_xubio.ErrorPantallaXubio, match="no hay una cuenta de Percepción de IVA sufrida"):
        libro_mayor_xubio.recorrer(pestana, date(2026, 10, 1), date(2026, 10, 31), "IVA", "Percepcion")


def test_va_directo_a_contabilidad_libro_mayor(pestana, pantalla, monkeypatch):
    monkeypatch.setattr(settings, "XUBIO_WEB_URL", pantalla)
    monkeypatch.setattr(settings, "XUBIO_URL_LIBRO_MAYOR", "")
    pestana.goto(pantalla + "/NXV/home")
    _Xubio.rutas.clear()
    libro_mayor_xubio.recorrer(pestana, date(2026, 10, 1), date(2026, 10, 31), "Ganancias", "Retencion")
    assert [r for r in _Xubio.rutas if r.startswith("/NXV")][0] == "/NXV/contabilidad/libro-mayor-ar"


def test_lee_la_empresa_activa(pestana, pantalla, monkeypatch):
    monkeypatch.setattr(settings, "XUBIO_WEB_URL", pantalla)
    _Xubio.activa = "tnt_2"
    pestana.goto(pantalla + "/NXV/home")
    assert libro_mayor_xubio.empresa_activa(pestana) == "MATERIALES SAN FERNANDO S.A."


def test_del_reporte_se_leen_las_columnas_de_pantalla_como_el_excel():
    # Como el de Xubio: columnas visibles (las del Excel) y ocultas; el CUIT
    # oculto es el de la empresa y DETALLE no es la descripcion.
    campos = [("FECHA", "VISIBLE", "Fecha"), ("DOCUMENTO", "VISIBLE", "Documento"), ("CUENTA", "VISIBLE", "Cuenta"),
              ("DEBEMONPRINCIPAL", "VISIBLE", "Debe"), ("HABERMONPRINCIPAL", "VISIBLE", "Haber"),
              ("DESCRIPCION", "VISIBLE", "Descripción"), ("ORGANIZACION", "VISIBLE", "Organización"),
              ("CUIT", "NOTVISIBLE", "CUIT"), ("DETALLE", "NOTVISIBLE", "Detalle")]
    fields = "".join(f'<field name="{n}" type="{t}" caption="{c}"/>' for n, t, c in campos)
    filas = [
        "1,01-01-2026,Asiento Manual N° 1,1.1.3.18 Retención Ganancias Sufrida,500.00,,"
        "Asiento de apertura patrimonial,,30-71000000-1,",
        "2,08-01-2026,Cobranza N° X-0001-00000001,1.1.3.18 Retención Ganancias Sufrida,100.50,,,"
        "Cliente SRL,30-71000000-1,Factura N° 9",
    ]
    xml = f"<root><dataset><fields>{fields}</fields><data><![CDATA[{';'.join(filas)}]]></data></dataset></root>"

    mayor = leer_mayor_desde_webreport(xml, vacio_ok=True)

    assert [(m["importe"], m["razon_social"], m["cuit"], m["descripcion"]) for m in mayor.retenciones] == [
        (100.5, "Cliente SRL", None, "")
    ]
