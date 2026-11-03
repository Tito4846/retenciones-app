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


def test_flujo_completo_con_exportacion(ruta_arca, ruta_mayor, leer_hoja):
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

    conciliados = leer_hoja(excel.content, "Conciliados")
    assert list(conciliados["Importe"].dropna())[:2] == [32110.0, 16100.0]
    faltan_en_mayor = leer_hoja(excel.content, "Faltan_en_Mayor")
    assert set(faltan_en_mayor["Estado"].dropna()) == {"Sin coincidencia"}
    assert openpyxl.load_workbook(io.BytesIO(excel.content)).sheetnames[:4] == [
        "Resumen", "Conciliados", "Faltan_en_ARCA", "Faltan_en_Mayor"]
    assert [c["categoria"] for c in estado["categorias"]] == ["A", "B", "C"]


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


def test_emparejar_a_mano_y_deshacer(ruta_arca, ruta_mayor, leer_hoja):
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
    conciliados = leer_hoja(cliente.get(f"/api/exportar/{task_id}").content, "Conciliados")
    assert "Conciliado a mano" in set(conciliados["Criterio"].dropna())

    # Un lado ya conciliado no se puede volver a usar.
    assert cliente.post(f"/api/conciliacion/{task_id}/pares", json=par).status_code == 409

    deshecho = cliente.delete(f"/api/conciliacion/{task_id}/pares/{par['arca_id']}").json()
    assert deshecho["resumen"]["total_conciliados"] == 2
    assert len(deshecho["sugerencias"]) == 1


def test_emparejar_conciliacion_inexistente():
    respuesta = cliente.post("/api/conciliacion/no-existe/pares", json={"arca_id": "a0", "mayor_id": "m0"})
    assert respuesta.status_code == 404
