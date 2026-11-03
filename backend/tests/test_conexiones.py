"""
Bots de las conexiones, sin Chrome: Playwright se reemplaza por objetos de
mentira. Que cada selector caiga donde tiene que caer se valida contra ARCA y
Xubio reales; aca se prueba la logica alrededor (estados, descargas, mayor).
"""

import time
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.bots import conexiones
from app.bots.navegador_arca import CONECTADO, DESCONECTADO, NavegadorArca
from app.bots.navegador_xubio import en_ingreso
from app.main import app
from app.services.importador_mayor import leer_mayor_desde_webreport
from app.services.tools import ErrorDeLectura
from tests.conftest import ARCA_CSV

cliente = TestClient(app)

WEBREPORT = """<?xml version="1.0" encoding="UTF-8"?>
<root><dataset>
<fields><field name="FECHA"/><field name="CUENTA"/><field name="DEBE"/><field name="HABER"/><field name="DESCRIPCION"/><field name="ORGANIZACION"/></fields>
<data>1,2026-10-16,Retenciones IVA Sufridas,32110.00,0,Cobro Cert. 12345,Banco Ejemplo S.A.;2,2026-10-11,Retenciones Ganancias Sufridas,16100.00,0,Cobro factura 33,Perez Juan;3,2026-10-11,Banco Galicia CC,16100.00,0,Cobro factura 33,Perez Juan;4,2026-10-31,Retenciones IVA Sufridas,0,40000.00,Aplicacion DDJJ,</data>
</dataset></root>"""


def _esperar(condicion, segundos=5):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.05)
    return False


# ----------------------------------------------------------------------
# Libro Mayor por webreport
# ----------------------------------------------------------------------

def test_webreport_filtra_cuentas_de_retenciones():
    lectura = leer_mayor_desde_webreport(WEBREPORT)
    assert [r["importe"] for r in lectura.retenciones] == [32110.0, 16100.0]
    assert lectura.retenciones[0]["impuesto"] == "IVA"
    assert lectura.retenciones[1]["razon_social"] == "Perez Juan"
    # La aplicacion a la DDJJ (Haber) no se cruza; el banco no es retencion.
    assert len(lectura.aplicaciones) == 1


def test_webreport_sin_cuentas_de_retenciones():
    xml = WEBREPORT.replace("Retenciones IVA Sufridas", "Caja").replace("Retenciones Ganancias Sufridas", "Caja")
    with pytest.raises(ErrorDeLectura, match="cuentas de retenciones"):
        leer_mayor_desde_webreport(xml)


def test_webreport_respuesta_que_no_es_xml():
    with pytest.raises(ErrorDeLectura, match="formato esperado"):
        leer_mayor_desde_webreport("<html>Advertencia</html")


def test_en_ingreso_mira_el_dominio_y_no_el_texto():
    assert en_ingreso("https://connect.visma.com/?returnUrl=https://xubio.com/NXV")
    assert en_ingreso("https://xubio.com/NXV/vismaConnect/login")
    assert not en_ingreso("https://xubio.com/NXV/#home")


def test_periodo_del_mayor_estira_por_la_tolerancia():
    retenciones = [{"fecha": date(2026, 10, 4)}, {"fecha": date(2026, 10, 20)}]
    desde, hasta = conexiones.periodo_del_mayor(retenciones, None, None)
    assert desde < date(2026, 10, 4) and hasta > date(2026, 10, 20)
    assert conexiones.periodo_del_mayor(retenciones, date(2026, 10, 1), None)[0] == date(2026, 10, 1)


# ----------------------------------------------------------------------
# Bot de ARCA con Playwright de mentira
# ----------------------------------------------------------------------

class _Descarga:
    suggested_filename = "MisRetenciones.csv"

    def save_as(self, ruta):
        with open(ruta, "w", encoding="cp1252") as fh:
            fh.write(ARCA_CSV)


def _como_playwright(oyentes: list):
    """Igual que Playwright: le guarda un atributo al manejador (un built-in no lo admite)."""

    def on(evento, fn):
        setattr(fn, "_pw_impl_instance_", object())
        oyentes.append(fn)

    return on


def _chrome_falso(oyentes: list):
    pestana = MagicMock()
    pestana.url = "https://portalcf.cloud.afip.gob.ar/portal/app/"
    pestana.is_closed.return_value = False
    pestana.on.side_effect = _como_playwright(oyentes)
    contexto = MagicMock()
    contexto.on.side_effect = _como_playwright([])
    contexto.pages = [pestana]
    # "Mis Retenciones" esta entre los mas utilizados y se abre en otra pestana.
    pestana.context.expect_page.return_value.__enter__.return_value.value = pestana
    # La version nueva muestra el "click aqui" a la version anterior.
    pestana.frames = [pestana]
    pestana.get_by_role.return_value.filter.return_value.first.count.return_value = 1

    @contextmanager
    def lanzar():
        yield contexto, pestana

    return lanzar, pestana


def test_bot_arca_conecta_y_atrapa_la_exportacion(tmp_path):
    oyentes: list = []
    lanzar, pestana = _chrome_falso(oyentes)
    bot = NavegadorArca(lanzar=lanzar, directorio=tmp_path)

    estado = bot.conectar("30-71234567-8", "clave-de-prueba")
    assert estado["cuit"] == "30712345678"
    assert _esperar(lambda: bot.estado()["estado"] == CONECTADO)
    # La clave se escribio en el login y no quedo en el estado.
    assert "clave-de-prueba" not in str(bot.estado())

    # La persona toca "Exportar" en la ventana.
    oyentes[0](_Descarga())
    assert _esperar(lambda: bot.estado()["descarga"] is not None)
    assert bot.estado()["descarga"]["filas"] == 4
    assert bot.ultima_descarga().ruta.exists()

    bot.desconectar()
    assert _esperar(lambda: not bot._hilo.is_alive())
    assert bot.estado()["estado"] == DESCONECTADO
    # La descarga queda para conciliar aunque se cierre la ventana.
    assert bot.ultima_descarga() is not None


def test_bot_arca_detecta_el_formulario_si_se_entra_a_mano(tmp_path, monkeypatch):
    from app.bots.navegador_arca import MENSAJE_CONECTADO, MENSAJE_SIN_FORMULARIO
    from app.core.config import settings

    monkeypatch.setattr(settings, "TIMEOUT_NAVEGACION_MS", 0)
    lanzar, pestana = _chrome_falso([])
    # Sin "click aqui" y la direccion directa contesta 401 (pasa en ARCA real).
    pestana.frames = []
    directa = pestana.context.new_page.return_value
    directa.goto.return_value.ok = False
    directa.goto.return_value.status = 401
    opciones = pestana.locator.return_value.locator.return_value.evaluate_all
    opciones.return_value = []
    bot = NavegadorArca(lanzar=lanzar, directorio=tmp_path)
    bot.conectar("30712345678", "x")
    assert _esperar(lambda: bot.estado()["mensaje"] == MENSAJE_SIN_FORMULARIO)
    assert bot.formulario() is None
    directa.close.assert_called()

    # La persona toca "click aqui" en la ventana y llega al formulario.
    opciones.return_value = [{"valor": "30712345678", "texto": "30712345678"}]
    pestana.url = "https://seti.afip.gob.ar/misret/consultaMisRetenciones.do?method=getForm"
    pestana.locator.return_value.count.return_value = 1
    assert _esperar(lambda: bot.estado()["mensaje"] == MENSAJE_CONECTADO)
    assert bot.formulario()["cuits"] == ["30712345678"]
    bot.desconectar()


def test_bot_arca_cuit_invalida():
    from app.bots.navegador_arca import ErrorArca

    with pytest.raises(ErrorArca):
        NavegadorArca(lanzar=MagicMock()).conectar("123", "x")


def test_bot_arca_ventana_cerrada_a_mano(tmp_path):
    oyentes: list = []
    lanzar, pestana = _chrome_falso(oyentes)
    bot = NavegadorArca(lanzar=lanzar, directorio=tmp_path)
    bot.conectar("30712345678", "x")
    assert _esperar(lambda: bot.estado()["estado"] == CONECTADO)
    pestana.is_closed.return_value = True
    assert _esperar(lambda: bot.estado()["estado"] == DESCONECTADO)
    assert "Se cerró la ventana" in bot.estado()["mensaje"]


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

def test_estado_de_conexiones():
    datos = cliente.get("/api/conexiones").json()
    assert set(datos) == {"arca", "xubio"}
    assert datos["arca"]["estado"] == "desconectado"


def test_conciliar_sin_descarga_de_arca():
    assert cliente.post("/api/conexiones/conciliar").status_code == 409


def test_conciliar_desde_conexiones(tmp_path, monkeypatch):
    ruta = tmp_path / "arca.csv"
    ruta.write_text(ARCA_CSV, encoding="cp1252")
    descarga = MagicMock(ruta=ruta)
    monkeypatch.setattr(conexiones.arca, "ultima_descarga", lambda: descarga)
    monkeypatch.setattr(type(conexiones.xubio), "conectado", property(lambda self: True))
    pedidos = []
    monkeypatch.setattr(conexiones.xubio, "libro_mayor", lambda d, h: pedidos.append((d, h)) or WEBREPORT)

    task_id = cliente.post("/api/conexiones/conciliar").json()["task_id"]
    estado = cliente.get(f"/api/estado/{task_id}").json()

    assert estado["status"] == "completado", estado
    assert estado["resumen"]["total_conciliados"] == 2
    # El mayor se pidio con el rango de ARCA estirado por la tolerancia.
    assert pedidos[0][0] < date(2026, 10, 4) and pedidos[0][1] > date(2026, 10, 20)


def _reporte_de(cuenta: str) -> str:
    """El reporte de una sola cuenta, como lo carga Xubio al tocar Aceptar."""
    inicio, resto = WEBREPORT.split("<data>")
    filas, cierre = resto.split("</data>")
    propias = ";".join(f for f in filas.split(";") if f"," + cuenta + "," in f)
    return f"{inicio}<data>{propias}</data>{cierre}"


def _automatico(tmp_path, monkeypatch, pantalla):
    from app.bots import misret

    ruta = tmp_path / "arca.csv"
    ruta.write_text(ARCA_CSV, encoding="cp1252")
    monkeypatch.setattr(
        conexiones.arca,
        "estado",
        lambda: {"estado": "conectado", "formulario": {"cuits": ["30710809883"]}, "consultando": False},
    )
    monkeypatch.setattr(
        conexiones.arca,
        "formulario",
        lambda: {"impuestos": [{"codigo": "217", "descripcion": "GANANCIAS"}, {"codigo": "767", "descripcion": "IVA"}]},
    )
    monkeypatch.setattr(type(conexiones.xubio), "conectado", property(lambda self: True))
    orden = []

    def consultar_y_esperar(consulta: misret.ConsultaArca, avance):
        orden.append(("arca", consulta.impuestos))
        return MagicMock(ruta=ruta, filas=4)

    def por_pantalla(desde, hasta, impuesto, tipo, avance):
        orden.append(("xubio", desde, hasta, impuesto, tipo))
        return pantalla(impuesto)

    monkeypatch.setattr(conexiones.arca, "consultar_y_esperar", consultar_y_esperar)
    monkeypatch.setattr(conexiones.xubio, "libro_mayor_por_pantalla", por_pantalla)
    monkeypatch.setattr(conexiones.xubio, "libro_mayor", lambda d, h: orden.append(("reporte", d, h)) or WEBREPORT)

    respuesta = cliente.post(
        "/api/conexiones/automatico",
        json={"cuit_retenido": "30710809883", "desde": "2026-10-01", "hasta": "2026-10-31"},
    )
    assert respuesta.status_code == 202, respuesta.text
    return cliente.get(f"/api/estado/{respuesta.json()['task_id']}").json(), orden


def test_automatico_arca_primero_y_libro_mayor_por_pantalla(tmp_path, monkeypatch):
    cuentas = {"Ganancias": "Retenciones Ganancias Sufridas", "IVA": "Retenciones IVA Sufridas"}
    estado, orden = _automatico(tmp_path, monkeypatch, lambda imp: [(cuentas[imp], _reporte_de(cuentas[imp]))])

    assert estado["status"] == "completado", estado
    assert estado["resumen"]["total_conciliados"] == 2
    # Primero ARCA (sin impuesto elegido: Ganancias e IVA); despues Xubio con
    # las mismas fechas, estiradas en la tolerancia, cuenta por impuesto.
    assert orden == [
        ("arca", ["217", "767"]),
        ("xubio", date(2026, 10, 1), date(2026, 11, 5), "Ganancias", "RetencionPercepcion"),
        ("xubio", date(2026, 10, 1), date(2026, 11, 5), "IVA", "RetencionPercepcion"),
    ]


def test_automatico_si_la_pantalla_falla_usa_el_reporte(tmp_path, monkeypatch):
    from app.bots.libro_mayor_xubio import ErrorPantallaXubio

    def falla(impuesto):
        raise ErrorPantallaXubio("No apareció «Elige los filtros».")

    estado, orden = _automatico(tmp_path, monkeypatch, falla)
    assert estado["status"] == "completado", estado
    assert estado["resumen"]["total_conciliados"] == 2
    assert orden[-1] == ("reporte", date(2026, 10, 1), date(2026, 11, 5))


def test_automatico_sin_conexiones():
    respuesta = cliente.post(
        "/api/conexiones/automatico",
        json={"cuit_retenido": "30710809883", "desde": "2026-10-01", "hasta": "2026-10-31"},
    )
    assert respuesta.status_code == 409


def test_automatico_valida_fechas():
    respuesta = cliente.post(
        "/api/conexiones/automatico",
        json={"cuit_retenido": "30710809883", "desde": "2026-11-01", "hasta": "2026-10-31"},
    )
    assert respuesta.status_code == 422
