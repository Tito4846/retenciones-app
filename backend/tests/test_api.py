import io
from urllib.parse import unquote

import openpyxl
from fastapi.testclient import TestClient

from app.main import app

cliente = TestClient(app)


def _subir(ruta_arca, ruta_mayor):
    with open(ruta_arca, "rb") as a, open(ruta_mayor, "rb") as m:
        return cliente.post(
            "/api/conciliar",
            files={"archivo_arca": ("arca.csv", a), "archivo_mayor": ("mayor.xlsx", m)},
        )


def test_flujo_completo_con_exportacion(ruta_arca, ruta_mayor):
    respuesta = _subir(ruta_arca, ruta_mayor)
    assert respuesta.status_code == 202
    task_id = respuesta.json()["task_id"]

    # TestClient corre las BackgroundTasks antes de devolver el POST.
    estado = cliente.get(f"/api/estado/{task_id}").json()
    assert estado["status"] == "completado"
    assert estado["resumen"]["total_conciliados"] == 2
    assert set(estado["detalle"]) == {"conciliados", "faltan_en_arca", "faltan_en_mayor"}

    excel = cliente.get(f"/api/exportar/{task_id}", params={"empresa": "Piccinini & Asoc."})
    assert excel.status_code == 200
    assert "Piccinini" in unquote(excel.headers["content-disposition"])

    libro = openpyxl.load_workbook(io.BytesIO(excel.content))
    assert libro.sheetnames == ["ARCA", "LIBRO MAYOR"]

    # ARCA: el export tal cual, lo conciliado en amarillo y el total dos filas abajo.
    arca = libro["ARCA"]
    assert arca["A1"].value == "CUIT Agente Ret./Perc." and arca["H1"].value == "Importe Ret./Perc."
    assert [arca.cell(row=f, column=8).value for f in range(2, 6)] == [32110.0, 16100.0, 8300.75, 1500.0]
    assert _amarillos(arca, "H") == [32110.0, 16100.0]
    assert arca["H8"].value == "=SUM(H2:H5)"

    # LIBRO MAYOR: el mayor tal cual (con el saldo inicial y la aplicacion del Haber) y,
    # debajo, cada apartado con su titulo: lo que falta en el mayor (Debe) y lo que
    # falta en ARCA (Haber). Al pie, saldo ARCA, saldo Xubio y la diferencia.
    mayor = libro["LIBRO MAYOR"]
    assert [c.value for c in mayor[1]] == ["Fecha", "Documento", "Cuenta", "Debe", "Haber", "Descripción", "Organización"]
    assert [mayor.cell(row=f, column=6).value for f in range(2, 7)] == [
        "Saldo Inicial", "Cobro Cert. 12345", "Cobro factura 33", "Cobro", "Aplicación DDJJ IVA"]
    assert _amarillos(mayor, "D") == [32110.0, 16100.0]
    assert mayor["E6"].value == 40000.0
    assert mayor["A9"].value == "PARTIDAS DE ARCA PENDIENTES DE REGISTRACIÓN" and mayor["A9"].font.bold
    assert [(mayor.cell(row=f, column=1).value, mayor.cell(row=f, column=2).value, mayor.cell(row=f, column=4).value)
            for f in (10, 11)] == [("RETENCION", "PROVEEDOR SRL", 8300.75), ("RETENCION", "PROVEEDOR SRL", 1500.0)]
    assert mayor["A14"].value == "PARTIDAS CONTABILIZADAS - REVISAR PROCEDENCIA / REVISAR CLIENTE"
    assert (mayor["B15"].value, mayor["D15"].value, mayor["E15"].value) == ("REC 0001-00004530", None, 1500.0)
    # Sin columna Saldo en este mayor: el saldo Xubio es Debe menos Haber de todo lo de arriba.
    assert [(mayor.cell(row=f, column=4).value, mayor.cell(row=f, column=5).value) for f in (18, 19, 20)] == [
        ("SALDO ARCA:", "='ARCA'!H8"),
        ("SALDO XUBIO:", "=SUM(D2:D17)-SUM(E2:E17)"),
        ("DIF:", "=E18-E19"),
    ]
    # El control no da 0 por la aplicacion a la DDJJ (Haber), que ARCA no informa:
    # la DIF y el motivo al pie van en rojo; las filas del mayor y de ARCA, no.
    assert _rojo(mayor["E20"])
    assert mayor["A22"].value.startswith("MOTIVOS DE LA DIFERENCIA") and _rojo(mayor["A22"])
    assert mayor["A23"].value.startswith("Aplicaciones del Haber") and mayor["E23"].value == 40000.0
    assert not _rojo(mayor["E6"]) and not any(_rojo(c) for c in arca["H"])
    assert mayor.max_row == 23

    # El cuadro de la pantalla: ARCA − Mayor = lo pendiente de registrar menos lo que ARCA no informa.
    motivos = estado["resumen"]["motivos_diferencia"]
    assert [(m["motivo"], m["cantidad"], m["importe"]) for m in motivos] == [
        ("faltan_en_mayor", 2, 9800.75), ("faltan_en_arca", 1, -1500.0)]
    assert sum(m["importe"] for m in motivos) == estado["resumen"]["diferencia"]
    assert [c["categoria"] for c in estado["categorias"]] == ["A", "B", "C"]


def _amarillos(hoja, columna: str) -> list:
    return [c.value for c in hoja[columna] if c.fill.fill_type == "solid"]


def _rojo(celda) -> bool:
    return celda.font.color is not None and celda.font.color.rgb == "FFC00000"


def test_archivo_ilegible_termina_en_error(tmp_path, ruta_mayor):
    malo = tmp_path / "arca.csv"
    malo.write_text("hola;mundo\n1;2\n", encoding="utf-8")
    task_id = _subir(str(malo), ruta_mayor).json()["task_id"]
    estado = cliente.get(f"/api/estado/{task_id}").json()
    assert estado["status"] == "error"
    assert estado["mensaje"].startswith("Archivo de ARCA:")


def test_extension_no_soportada(tmp_path, ruta_mayor):
    pdf = tmp_path / "arca.pdf"
    pdf.write_bytes(b"%PDF")
    with open(pdf, "rb") as a, open(ruta_mayor, "rb") as m:
        r = cliente.post("/api/conciliar", files={"archivo_arca": ("arca.pdf", a), "archivo_mayor": ("m.xlsx", m)})
    assert r.status_code == 400


def test_estado_inexistente():
    assert cliente.get("/api/estado/no-existe").status_code == 404


def test_emparejar_a_mano_y_deshacer(ruta_arca, ruta_mayor):
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    estado = cliente.get(f"/api/estado/{task_id}").json()

    # Ganancias de PROVEEDOR SRL por 1.500 en ARCA y un asiento de 1.500 en la
    # cuenta de IVA: el cruce no los junta solo, pero los sugiere.
    (sugerencia,) = estado["sugerencias"]
    assert "otra cuenta" in sugerencia["motivo"] or "cuenta de IVA" in sugerencia["motivo"]
    assert estado["por_impuesto"][0]["impuesto"] == "IVA"

    par = {"arca_id": sugerencia["id_arca"], "mayor_id": sugerencia["id_mayor"]}
    nuevo = cliente.post(f"/api/conciliacion/{task_id}/pares", json=par).json()
    assert nuevo["resumen"]["total_conciliados"] == 3
    assert nuevo["resumen"]["total_manuales"] == 1
    assert nuevo["sugerencias"] == []
    # El estado y el Excel ya tienen el par.
    assert cliente.get(f"/api/estado/{task_id}").json()["resumen"]["total_conciliados"] == 3
    libro = openpyxl.load_workbook(io.BytesIO(cliente.get(f"/api/exportar/{task_id}").content))
    assert sorted(_amarillos(libro["ARCA"], "H")) == [1500.0, 16100.0, 32110.0]

    # Un lado ya conciliado no se puede volver a usar.
    assert cliente.post(f"/api/conciliacion/{task_id}/pares", json=par).status_code == 409

    deshecho = cliente.delete(f"/api/conciliacion/{task_id}/pares/{par['arca_id']}").json()
    assert deshecho["resumen"]["total_conciliados"] == 2
    assert len(deshecho["sugerencias"]) == 1


def test_guardar_ediciones_y_movimientos_nuevos(ruta_arca, ruta_mayor):
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    estado = cliente.get(f"/api/estado/{task_id}").json()
    (asiento,) = estado["detalle"]["faltan_en_arca"]
    assert asiento["importe"] == 1500.0

    # Se corrige el asiento sin par y se agrega en el mayor el que faltaba de los
    # 8.300,75 de PROVEEDOR SRL: el cruce lo concilia por el certificado.
    nuevo = cliente.post(f"/api/conciliacion/{task_id}/movimientos", json={
        "cambios": {asiento["id"]: {"razon_social": "Proveedor SRL (corregido)", "importe": "1600"}},
        "nuevos": [{"lado": "mayor", "fecha": "2026-10-20", "razon_social": "Proveedor SRL", "impuesto": "IVA",
                    "tipo": "Retencion", "comprobante": "REC 0001-00009999 Cert. 77777", "importe": 8300.75}],
    }).json()
    assert nuevo["resumen"]["total_conciliados"] == 3
    (editado,) = nuevo["detalle"]["faltan_en_arca"]
    assert (editado["id"], editado["razon_social"], editado["importe"]) == (asiento["id"], "Proveedor SRL (corregido)", 1600.0)

    # El Excel sale con lo guardado: el asiento nuevo al pie del mayor, conciliado.
    libro = openpyxl.load_workbook(io.BytesIO(cliente.get(f"/api/exportar/{task_id}").content))
    mayor = libro["LIBRO MAYOR"]
    valores = [[c.value for c in fila] for fila in mayor.iter_rows()]
    assert any("REC 0001-00009999 Cert. 77777" in fila for fila in valores)
    assert any(1600.0 in fila and "Proveedor SRL (corregido)" in fila for fila in valores)

    malo = cliente.post(f"/api/conciliacion/{task_id}/movimientos", json={"cambios": {asiento["id"]: {"importe": "-5"}}})
    assert malo.status_code == 422

    # Quitar el asiento sin par: B queda vacio y el Excel ya no lo tiene.
    sin_asiento = cliente.post(f"/api/conciliacion/{task_id}/movimientos", json={"eliminados": [asiento["id"]]}).json()
    assert sin_asiento["detalle"]["faltan_en_arca"] == []
    assert sin_asiento["resumen"]["total_conciliados"] == 3
    libro = openpyxl.load_workbook(io.BytesIO(cliente.get(f"/api/exportar/{task_id}").content))
    assert not any("Proveedor SRL (corregido)" in [c.value for c in fila] for fila in libro["LIBRO MAYOR"].iter_rows())


def test_quitar_respeta_los_pares_hechos_a_mano(ruta_arca, ruta_mayor):
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    (s,) = cliente.get(f"/api/estado/{task_id}").json()["sugerencias"]
    cliente.post(f"/api/conciliacion/{task_id}/pares", json={"arca_id": s["id_arca"], "mayor_id": s["id_mayor"]})
    # Se quita una retencion de ARCA anterior al par: el par sigue, con sus ids nuevos.
    quitar = "a0" if s["id_arca"] != "a0" else "a1"
    nuevo = cliente.post(f"/api/conciliacion/{task_id}/movimientos", json={"eliminados": [quitar]}).json()
    assert nuevo["resumen"]["total_manuales"] == 1


def test_emparejar_conciliacion_inexistente():
    respuesta = cliente.post("/api/conciliacion/no-existe/pares", json={"arca_id": "a0", "mayor_id": "m0"})
    assert respuesta.status_code == 404
