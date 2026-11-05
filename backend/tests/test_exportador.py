"""El Excel de salida con el mayor que trae el bot (webreport) y casos borde."""

import io
from datetime import date

import openpyxl

from app.services import exportador, tareas
from app.services.importador_mayor import leer_mayor_desde_webreport
from app.services.tareas import Entrada, _cruzar

ARCA = [
    {"cuit": "30500000001", "razon_social": "BANCO EJEMPLO SA", "fecha": date(2026, 10, 4), "importe": 32110.0,
     "impuesto": "IVA", "certificado": "12345", "comprobante": None, "regimen": None, "tipo": "Retencion"},
]

# Con los titulos de Xubio (caption) y un cobro del 3/11 que no es del mes.
WEBREPORT = """<?xml version="1.0" encoding="UTF-8"?>
<root><dataset>
<fields><field name="FECHA" caption="Fecha" type="VISIBLE"/><field name="CUENTA" caption="Cuenta" type="VISIBLE"/><field name="DEBE" caption="Debe" type="VISIBLE"/><field name="HABER" caption="Haber" type="VISIBLE"/><field name="DESCRIPCION" caption="Descripción" type="VISIBLE"/><field name="ORGANIZACION" caption="Organización" type="VISIBLE"/></fields>
<data>1,2026-10-16,Retenciones IVA Sufridas,32110.00,0,Cobro Cert. 12345,Banco Ejemplo S.A.;2,2026-11-03,Retenciones IVA Sufridas,500.00,0,=Cobro noviembre,Otro SA</data>
</dataset></root>"""


def _libro(hasta_periodo):
    entrada = Entrada(ARCA, leer_mayor_desde_webreport(WEBREPORT), hasta_periodo)
    contenido = exportador.exportar(_cruzar(entrada, {}), entrada)
    return openpyxl.load_workbook(io.BytesIO(contenido))["LIBRO MAYOR"]


def test_mayor_del_bot_con_los_titulos_de_xubio_y_sin_el_mes_siguiente():
    hoja = _libro(date(2026, 10, 31))
    assert [c.value for c in hoja[1]][:4] == ["Fecha", "Cuenta", "Debe", "Haber"]
    assert hoja["C2"].value == 32110.0 and hoja["C2"].fill.fill_type == "solid"
    # El cobro del 3/11 no se concilio y es del mes siguiente: no va. Despues, solo el control.
    assert hoja["A3"].value is None and hoja["C5"].value == "SALDO ARCA:"
    assert hoja.max_row == 7


def test_texto_que_empieza_con_igual_no_es_formula():
    hoja = _libro(None)
    assert hoja["E3"].value == "=Cobro noviembre"
    assert hoja["E3"].data_type == "s"


def test_el_cliente_que_saben_los_bots_sigue_despues_de_editar():
    task_id = tareas.crear()
    tareas.procesar_con(task_id, lambda: (ARCA, leer_mayor_desde_webreport(WEBREPORT)), date(2026, 10, 31),
                        identidad=lambda: ("Piccinini SA", "30711111119"))
    assert tareas.obtener(task_id).status == "completado"
    resumen = tareas.guardar_cambios(task_id, {}, [], [])["resumen"]
    assert (resumen["empresa"], resumen["cuit_empresa"], resumen["periodo"]) == ("Piccinini SA", "30-71111111-9", "2026-10")
