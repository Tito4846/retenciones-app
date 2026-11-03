"""
El bot de Mis Retenciones contra una copia local del formulario de ARCA (mismos
nombres de campos que el HTML real capturado, validacion con alert y POST a
una pagina de resultados), manejada con el Chrome real. Se saltea si no hay
Chrome instalado.
"""

import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from app.bots import misret
from app.services.importador_arca import leer_arca

FORMULARIO = """<html><body>
<form name="formulario" action="consultaMisRetenciones.do" method="post"
      onsubmit="if (!this.fechaRetencionDesde.value) { alert('Debe ingresar la fecha desde'); return false; }">
  <input type="hidden" name="method" value="lookup">
  <select id="cuitRetenido" name="cuitRetenido">
    <option value="">Seleccione una CUIT...</option>
    <option value="20235102162">20235102162</option>
    <option value="30710809883">30710809883</option>
  </select>
  <input id="cuitAgente" name="cuitAgente" type="text" maxlength="11">
  <input name="impuesto" type="text" maxlength="4">
  <select id="impuestos" onchange="formulario.impuesto.value = this.value; setRetencionPercepcion(this.value)">
    <option value=""></option>
    <option value="217">217 - SICORE-IMPTO.A LAS GANANCIAS</option>
    <option value="767">767 - SICORE - RETENCIONES Y PERCEPC</option>
    <option value="216">216 - SIRE -  IVA</option>
  </select>
  <input name="retencionPercepcion" type="radio" value="Retencion" style="visibility:hidden">
  <input name="retencionPercepcion" type="radio" value="Percepcion" style="visibility:hidden">
  <input name="retencionPercepcion" type="radio" value="RetencionPercepcion" checked>
  <input name="fechaRetencionDesde" type="text" maxlength="8" value="01012026">
  <input name="fechaRetencionHasta" type="text" maxlength="8" value="02112026">
  <input class="inputbutton" type="submit" value="Consultar">
</form>
<script>
  // Como ARCA: con el 767 se puede elegir Retencion o Percepcion; con el 217,
  // solo el combinado; con el 216, solo Retencion (y ARCA la deja marcada).
  function setRetencionPercepcion(impuesto) {
    document.querySelectorAll('input[name=retencionPercepcion]').forEach(function (r) {
      if (impuesto === '216') {
        r.style.visibility = r.value === 'Retencion' ? 'visible' : 'hidden';
        r.checked = r.value === 'Retencion';
      } else if (r.value !== 'RetencionPercepcion') {
        r.style.visibility = impuesto === '767' ? 'visible' : 'hidden';
      } else {
        r.style.visibility = 'visible';
      }
    });
  }
</script></body></html>"""

FILAS = {
    "217": [("30-50000000-1", "BANCO EJEMPLO SA", "04/10/2026", "55555", "16.100,00")],
    "767": [
        ("30-71234567-8", "PROVEEDOR SRL", "20/10/2026", "77777", "8.300,75"),
        ("30-50000000-1", "BANCO EJEMPLO SA", "21/10/2026", "77778", "1.000,00"),
    ],
}


def _resultados(datos: dict) -> str:
    impuesto = datos["impuesto"][0]
    filas = "".join(
        f"<tr><td>{c}</td><td>{n}</td><td>{impuesto}</td><td>{f}</td><td>{cert}</td><td>{imp}</td></tr>"
        for c, n, f, cert, imp in FILAS.get(impuesto, [])
    )
    if not filas:
        return "<html><body>No se encontraron retenciones para los datos ingresados.</body></html>"
    return f"""<html><body><table>
      <tr><th>CUIT Agente</th><th>Denominación</th><th>Impuesto</th><th>Fecha Ret./Perc.</th>
          <th>Nro. Certificado</th><th>Importe Ret./Perc.</th></tr>{filas}</table></body></html>"""


class _Arca(BaseHTTPRequestHandler):
    recibidos: list[dict] = []

    def _html(self, cuerpo: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(cuerpo.encode("utf-8"))

    def do_GET(self):  # noqa: N802
        self._html(FORMULARIO)

    def do_POST(self):  # noqa: N802
        largo = int(self.headers.get("Content-Length", 0))
        datos = parse_qs(self.rfile.read(largo).decode("utf-8"), keep_blank_values=True)
        _Arca.recibidos.append(datos)
        self._html(_resultados(datos))

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def servidor():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Arca)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/misret/consultaMisRetenciones.do?method=getForm"
    httpd.shutdown()


@pytest.fixture(scope="module")
def pestana():
    playwright = pytest.importorskip("playwright.sync_api").sync_playwright().start()
    try:
        navegador = playwright.chromium.launch(headless=True, channel="chrome")
    except Exception as error:  # noqa: BLE001
        playwright.stop()
        pytest.skip(f"No hay Chrome para la prueba: {error}")
    pagina = navegador.new_page()
    avisos: list[str] = []

    def al_dialogo(dialogo):
        avisos.append(dialogo.message)
        dialogo.dismiss()

    pagina.on("dialog", al_dialogo)
    pagina.avisos = avisos
    yield pagina
    navegador.close()
    playwright.stop()


def _consulta(**cambios):
    datos = dict(cuit_retenido="30710809883", impuestos=["217"], desde=date(2026, 10, 1), hasta=date(2026, 10, 31))
    datos.update(cambios)
    return misret.ConsultaArca(**datos)


def test_lee_las_opciones_del_formulario(pestana, servidor):
    pestana.goto(servidor)
    formulario = misret.leer_formulario(pestana)
    assert formulario["cuits"] == ["20235102162", "30710809883"]
    assert formulario["impuestos"][0] == {
        "codigo": "217",
        "descripcion": "217 - SICORE-IMPTO.A LAS GANANCIAS",
        # Como en ARCA: con el 217 solo queda el combinado.
        "tipos": ["RetencionPercepcion"],
    }
    assert formulario["impuestos"][1]["tipos"] == ["Retencion", "Percepcion", "RetencionPercepcion"]


def test_completa_envia_y_lee_la_tabla(pestana, servidor, tmp_path):
    pestana.goto(servidor)
    _Arca.recibidos.clear()
    consulta = _consulta(cuit_agente="30500000001", tipo="Retencion")

    filas = misret.consultar_impuesto(pestana, servidor, consulta, "767", tmp_path, pestana.avisos)

    enviado = _Arca.recibidos[-1]
    assert enviado["cuitRetenido"] == ["30710809883"]
    assert enviado["cuitAgente"] == ["30500000001"]
    assert enviado["impuesto"] == ["767"]
    assert enviado["retencionPercepcion"] == ["Retencion"]
    assert enviado["fechaRetencionDesde"] == ["01102026"]
    assert enviado["fechaRetencionHasta"] == ["31102026"]
    assert [f["importe"] for f in filas] == [8300.75, 1000.0]
    assert filas[0]["cuit"] == "30712345678"
    assert filas[0]["certificado"] == "77777"


def test_vuelve_al_formulario_para_el_siguiente_impuesto(pestana, servidor, tmp_path):
    # La pestana quedo en los resultados de la prueba anterior. El 217 no
    # admite "Retencion" sola en ARCA: se consulta combinado.
    consulta = _consulta(tipo="Retencion")
    filas = misret.consultar_impuesto(pestana, servidor, consulta, "217", tmp_path, pestana.avisos)
    assert [f["importe"] for f in filas] == [16100.0]
    assert _Arca.recibidos[-1]["retencionPercepcion"] == ["RetencionPercepcion"]


def test_impuesto_que_solo_admite_retencion_usa_el_tipo_de_arca(pestana, servidor, tmp_path):
    # Se pide el combinado (lo que manda la pantalla) y el 216 no lo admite.
    pestana.goto(servidor)
    misret.consultar_impuesto(pestana, servidor, _consulta(), "216", tmp_path, pestana.avisos)
    assert _Arca.recibidos[-1]["impuesto"] == ["216"]
    assert _Arca.recibidos[-1]["retencionPercepcion"] == ["Retencion"]


def test_sin_resultados_devuelve_lista_vacia(pestana, servidor, tmp_path):
    FILAS["999"] = []
    pestana.goto(servidor)
    pestana.evaluate(
        "() => { const o = document.createElement('option'); o.value = '999'; o.text = '999';"
        " document.querySelector('#impuestos').appendChild(o); }"
    )
    assert misret.consultar_impuesto(pestana, servidor, _consulta(), "999", tmp_path, pestana.avisos) == []


def test_el_alert_de_arca_llega_como_error(pestana, servidor, tmp_path):
    pestana.goto(servidor)
    misret._completar(pestana, _consulta(), "217")
    pestana.fill(misret.SEL_DESDE, "")
    with pytest.raises(misret.ErrorConsulta, match="Debe ingresar la fecha desde"):
        misret._enviar(pestana, pestana.avisos, "217", espera_s=5)


def test_resultado_combinado_se_lee_igual(tmp_path):
    retenciones = [
        {"cuit": "30500000001", "razon_social": "BANCO", "fecha": date(2026, 10, 4), "importe": 32110.0,
         "impuesto": "IVA", "certificado": "0001-00012345"},
        {"cuit": "20123456789", "razon_social": "JUAN", "fecha": date(2026, 10, 10), "importe": 16100.5,
         "impuesto": "Ganancias", "certificado": None},
    ]
    ruta = tmp_path / "combinado.csv"
    misret.escribir_resultado(retenciones, ruta)
    leidas = leer_arca(str(ruta))
    assert [(r["cuit"], r["importe"], r["impuesto"], r["fecha"]) for r in leidas] == [
        ("30500000001", 32110.0, "IVA", date(2026, 10, 4)),
        ("20123456789", 16100.5, "Ganancias", date(2026, 10, 10)),
    ]


def test_consulta_valida_fechas():
    with pytest.raises(misret.ErrorConsulta):
        _consulta(desde=date(2026, 11, 1), hasta=date(2026, 10, 1)).validar()


OPCIONES = [
    {"codigo": "217", "descripcion": "217 - SICORE-IMPTO.A LAS GANANCIAS"},
    {"codigo": "767", "descripcion": "767 - SICORE - RETENCIONES Y PERCEPC"},
    {"codigo": "353", "descripcion": "353 - RETENCIONES CONTRIB.SEG.SOCIAL (SUSS)"},
]


def test_impuestos_detectados_en_el_mayor():
    assert misret.impuestos_a_consultar(OPCIONES, {"IVA"}) == ["767"]
    # Sin codigo principal conocido se busca por el nombre en ARCA.
    assert misret.impuestos_a_consultar(OPCIONES, {"SUSS", "Ganancias"}) == ["217", "353"]


def test_sin_detectados_van_ganancias_e_iva():
    assert misret.impuestos_a_consultar(OPCIONES) == ["217", "767"]
    # Un impuesto del mayor que ARCA no tiene (IIBB es provincial) no deja la consulta vacia.
    assert misret.impuestos_a_consultar(OPCIONES, {"Ingresos Brutos"}) == ["217", "767"]
