import io
import json
import sqlite3

import openpyxl
import pytest

from app.core import db
from app.core.config import settings
from app.services import excel_lote, lotes, tareas
from app.services import partidas as P
from tests.conftest import ARCA_CSV
from tests.test_api import _subir, cliente

ANA = {"X-Usuario-Id": "ana", "X-Usuario-Nombre": "Ana", "X-Usuario-Nivel": "10", "X-Usuario-Modulos": "Impuestos/Retenciones"}
LUIS = {"X-Usuario-Id": "luis", "X-Usuario-Nombre": "Luis", "X-Usuario-Nivel": "100"}
# Supervisora que preparó el lote: igual no lo puede revisar.
EVA = {"X-Usuario-Id": "eva", "X-Usuario-Nombre": "Eva", "X-Usuario-Nivel": "100"}
ADMIN = {"X-Usuario-Id": "root", "X-Usuario-Nombre": "Admin", "X-Usuario-Nivel": "100"}


@pytest.fixture(autouse=True)
def datos_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DIRECTORIO_DATOS", tmp_path / "datos")


def _lote(ruta_arca, ruta_mayor, quien=ANA) -> dict:
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    r = cliente.post("/api/lotes", json={"task_id": task_id, "empresa": "Piccinini", "cuit_empresa": "30-71111111-1",
                                         "periodo": "2026-10"}, headers=quien)
    assert r.status_code == 201, r.text
    return r.json()


def _justificar_todo(lote: dict, quien=ANA) -> dict:
    """Justifica cada partida pendiente y adjunta respaldo donde se exige."""
    lote_id = lote["lote"]["id"]
    for p in lote["partidas"]:
        if p["estado"] == "Conciliado Automático":
            continue
        version = lote["lote"]["version_vigente"]
        lote = cliente.patch(f"/api/lotes/{lote_id}/partidas/{p['clave']}",
                             json={"version": version, "justificacion": "ajuste", "comentario": "Ver respaldo",
                                   "importe_ajustado": p["importe_fisco"] or "0.00"}, headers=quien).json()
        r = cliente.post(f"/api/lotes/{lote_id}/adjuntos", data={"clave_partida": p["clave"]},
                         files={"archivo": ("nc.pdf", b"%PDF-1.4 respaldo " + p["clave"].encode(), "application/pdf")}, headers=quien)
        assert r.status_code == 201, r.text
        lote = r.json()
    return lote


def test_sin_rol_no_entra_a_los_lotes():
    for ruta in ("/api/lotes", "/api/conexiones", "/api/estado/x"):
        r = cliente.get(ruta, headers={"X-Usuario-Id": "ana", "X-Usuario-Nivel": "10"})
        assert r.status_code == 403 and r.json()["detail"]["codigo"] == "SIN_ROL", ruta


def test_partidas_con_estado_y_excel_con_hojas_del_lote(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    assert lote["lote"]["estado"] == "borrador" and lote["lote"]["version_vigente"] == 1
    estados = sorted((p["estado"], p["codigo"]) for p in lote["partidas"])
    # PEREZ: mismo agente e importe, aunque el asiento no traiga el certificado: conciliado.
    # BANCO: par por certificado pero a 12 días (P03);
    # PROVEEDOR: dos sin registrar (N01); el asiento de IVA de 1.500: no está en ARCA (N02).
    assert estados == [("Conciliado Automático", ""), ("No Localizado en ERP", "N01"), ("No Localizado en ERP", "N01"),
                       ("No Localizado en Fisco", "N02"), ("Pendiente de Comprobante", "P03")]
    # El cuadre al centavo contra las fuentes da.
    assert lote["versiones"][0]["validacion"]["valida"]
    assert not [e for e in lote["compuertas"]["enviar"] if e["codigo"].startswith("CUADRE")]
    # Sin justificar no frena nada: queda para quien aprueba.
    assert lote["compuertas"]["enviar"] == []
    assert {e["codigo"] for e in lote["pendientes"]} == {"SIN_JUSTIFICACION", "SIN_COMENTARIO", "SIN_RESPALDO", "DIFERENCIA_NETA"}

    excel = cliente.get(f"/api/lotes/{lote['lote']['id']}/versiones/1/excel", headers=ANA)
    libro = openpyxl.load_workbook(io.BytesIO(excel.content))
    assert libro.sheetnames == ["RESUMEN", "CONCILIACION", "ARCA", "LIBRO MAYOR", "_META"]
    assert libro.active.title == "RESUMEN"
    resumen = [c.value for fila in libro["RESUMEN"].iter_rows() for c in fila if c.value is not None]
    assert "Conciliaron solas 1 de 5 partidas. Quedan 4 para resolver en la hoja CONCILIACION." in resumen
    assert "JUAN PEREZ" not in resumen and "BANCO EJEMPLO SA" in resumen
    # Lo que hay que mirar queda arriba de lo que concilió solo.
    estados_excel = [libro["CONCILIACION"][f"B{f}"].value for f in range(2, 7)]
    assert estados_excel[-1] == "Conciliado Automático" and "Conciliado Automático" not in estados_excel[:-1]
    assert libro["_META"].sheet_state == "veryHidden" and libro["CONCILIACION"].protection.sheet
    assert [c.value for c in libro["CONCILIACION"][1]] == excel_lote.ENCABEZADOS


def test_ciclo_completo_maker_checker(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    base = f"/api/lotes/{lote_id}"

    # Un empleado no revisa.
    assert cliente.post(base + "/tomar", headers=ANA).status_code == 403

    lote = _justificar_todo(lote)
    assert lote["lote"]["version_vigente"] == 1  # borrador: se corrige en el lugar
    assert lote["compuertas"]["enviar"] == []
    lote = cliente.post(base + "/enviar", data={"version": 1}, headers=ANA).json()
    assert lote["lote"]["estado"] == "pendiente" and lote["versiones"][0]["sellada_utc"]

    # Mientras espera, la empleada no puede tocar nada.
    clave = lote["partidas"][0]["clave"]
    assert cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "x"}, headers=ANA).status_code == 409

    lote = cliente.post(base + "/tomar", headers=LUIS).json()
    assert lote["lote"]["estado"] == "en_revision" and lote["lote"]["checker_id"] == "luis"

    # Rechazo: el motivo es obligatorio.
    assert cliente.post(base + "/rechazar", json={"version": 1, "motivo": "mal"}, headers=LUIS).status_code == 422
    lote = cliente.post(base + "/rechazar", headers=LUIS, json={
        "version": 1, "motivo": "Diferencia no conciliada de $15.000 en certificado N° 4512",
        "observaciones": [{"texto": "El respaldo no corresponde", "origen": "nuestro", "clave": clave}]}).json()
    assert lote["lote"]["estado"] == "rechazado"
    notis = cliente.get("/api/notificaciones", headers=ANA).json()["notificaciones"]
    assert notis[0]["tipo"] == "rechazado" and "15.000" in notis[0]["texto"]

    # Corregir después de enviar crea la versión siguiente (v1 queda sellada).
    lote = cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "Corregido con la NC 12"}, headers=ANA).json()
    assert lote["lote"]["version_vigente"] == 2 and lote["lote"]["estado"] == "borrador"
    cambios = cliente.get(base + "/diff", params={"desde": 1, "hasta": 2}, headers=ANA).json()["cambiadas"]
    assert cambios == [{"clave": clave, "razon_social": lote["partidas"][0]["razon_social"],
                        "cambios": [{"campo": "comentario", "antes": "Ver respaldo", "despues": "Corregido con la NC 12"}]}]

    lote = cliente.post(base + "/enviar", data={"version": 2}, headers=ANA).json()
    cliente.post(base + "/tomar", headers=LUIS)

    assert cliente.post(base + "/aprobar", data={"version": 1}, headers=LUIS).json()["detail"]["codigo"] == "VERSION_VIEJA"
    lote = cliente.post(base + "/aprobar", data={"version": 2}, headers=LUIS).json()
    assert lote["lote"]["estado"] == "aprobado" and lote["lote"]["hash_aprobado"]

    # Aprobado = inmutable: ni la API ni la base dejan tocarlo.
    assert cliente.post(base + "/anular", json={"motivo": "Lo quiero anular igual"}, headers=ANA).status_code == 409
    assert cliente.post(f"{base}/adjuntos", files={"archivo": ("x.pdf", b"%PDF")}, headers=ANA).status_code == 409
    conexion = sqlite3.connect(db.ruta_db(), isolation_level=None)
    for sql in ("UPDATE lote SET empresa = 'otra' WHERE id = ?", "UPDATE partida SET comentario = 'x' WHERE lote_id = ?",
                "DELETE FROM adjunto WHERE lote_id = ?", "DELETE FROM auditoria WHERE lote_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            conexion.execute(sql, (lote_id,))
    conexion.close()

    # Exportación fiscal habilitada, idempotente.
    exp = cliente.post(f"{base}/exportaciones", json={"tipo": "ddjj_txt"}, headers=ANA).json()
    assert cliente.post(f"{base}/exportaciones", json={"tipo": "ddjj_txt"}, headers=ANA).json()["id"] == exp["id"]
    txt = cliente.get(f"{base}/exportaciones/{exp['id']}", headers=ANA).text
    assert txt.splitlines()[0] == "30500000001|04102026|0001-00012345|IVA|32110.00"

    # Auditoría completa y encadenada.
    acciones = [e["action_type"] for e in cliente.get(base + "/auditoria", headers=ANA).json()["eventos"]]
    for accion in ("lote_creado", "adjunto_subido", "enviado", "tomado", "rechazado", "aprobado", "exportacion", "descarga"):
        assert accion in acciones
    evento = cliente.get(base + "/auditoria", headers=ANA).json()["eventos"][0]
    assert evento["ts_utc"].endswith("Z") and evento["ip_address"]
    assert cliente.get("/api/auditoria/verificar", headers=ANA).status_code == 403
    assert cliente.get("/api/auditoria/verificar", headers=ADMIN).json()["ok"]


def test_segregacion_de_funciones(ruta_arca, ruta_mayor):
    lote = _justificar_todo(_lote(ruta_arca, ruta_mayor, EVA), EVA)
    base = f"/api/lotes/{lote['lote']['id']}"
    cliente.post(base + "/enviar", data={"version": 1}, headers=EVA)
    r = cliente.post(base + "/tomar", headers=EVA)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "SOD"
    acciones = [e["action_type"] for e in cliente.get(base + "/auditoria", headers=EVA).json()["eventos"]]
    assert "acceso_denegado" in acciones


def test_sin_comprobante_queda_como_pendiente(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    for p in lote["partidas"]:
        lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "justificacion": "error_carga", "comentario": "Ver NC"},
                             headers=ANA).json()
    assert {e["codigo"] for e in lote["pendientes"]} == {"SIN_RESPALDO", "DIFERENCIA_NETA"}
    assert cliente.post(f"{base}/adjuntos", files={"archivo": ("virus.exe", b"MZ")}, headers=ANA).status_code == 422


def _bajar(lote_id: str, numero: int):
    contenido = cliente.get(f"/api/lotes/{lote_id}/versiones/{numero}/excel", headers=ANA).content
    return openpyxl.load_workbook(io.BytesIO(contenido))


def _subir_version(lote_id: str, libro, version_base: int):
    salida = io.BytesIO()
    libro.save(salida)
    return cliente.post(f"/api/lotes/{lote_id}/versiones", data={"version_base": str(version_base)},
                        files={"archivo": ("trabajado.xlsx", salida.getvalue())}, headers=ANA)


def test_subir_version_trabajada_en_excel(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    hoja["P2"] = "Diferencia temporal (se registra en otro período)"
    hoja["Q2"] = "Se registra en noviembre"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    lote = r.json()
    assert lote["lote"]["version_vigente"] == 2 and lote["versiones"][0]["origen"] == "subido"
    assert (lote["partidas"][0]["justificacion"], lote["partidas"][0]["comentario"]) == ("temporal", "Se registra en noviembre")


def _avisos(lote: dict) -> list[str]:
    return [a["mensaje"] for a in lote["versiones"][0]["validacion"]["advertencias"] if a["etapa"] != 4]


def test_lo_que_se_toca_fuera_de_las_columnas_libres_se_ignora_y_se_avisa(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    importe = lote["partidas"][0]["importe_fisco"]
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    hoja["M2"] = 1.0  # Importe Fisco
    hoja["F2"] = "20-99999999-9"
    hoja["Q2"] = "Se registra en noviembre"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    lote = r.json()
    assert lote["lote"]["version_vigente"] == 2
    assert lote["partidas"][0]["importe_fisco"] == importe and lote["partidas"][0]["comentario"] == "Se registra en noviembre"
    assert _avisos(lote) == ["Fila 2: se mantuvo el dato original de «CUIT», «Importe Fisco»."]
    # Con la pantalla desactualizada, o el mismo archivo, no.
    assert _subir_version(lote_id, _bajar(lote_id, 2), 7).json()["detail"]["codigo"] == "VERSION_VIEJA"
    original = cliente.get(f"/api/lotes/{lote_id}/versiones/2/excel", headers=ANA).content
    r = cliente.post(f"/api/lotes/{lote_id}/versiones", data={"version_base": "2"},
                     files={"archivo": ("x.xlsx", original)}, headers=ANA)
    assert r.json()["detail"]["errores"][0]["codigo"] == "SIN_CAMBIOS"


def test_un_excel_ordenado_y_sin_hojas_tecnicas_se_carga_igual(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    clave = hoja["A3"].value
    # Otro orden de filas: la primera partida pasa abajo, con su comentario.
    hoja["Q3"] = "Va con la nota de crédito"
    hoja.move_range("A2:S2", rows=hoja.max_row + 2)
    del libro["_META"], libro["RESUMEN"], libro["ARCA"]
    hoja.delete_cols(4, 2)  # Flags y Criterio
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    lote = r.json()
    assert next(p for p in lote["partidas"] if p["clave"] == clave)["comentario"] == "Va con la nota de crédito"
    # Las hojas de origen salen de la versión anterior, no de lo que trajo el archivo.
    assert "ARCA" in _bajar(lote_id, 2).sheetnames


def test_las_filas_que_faltan_quedan_como_estaban_y_las_de_mas_se_ignoran(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    hoja["Q2"] = "Trabajada"
    hoja.delete_rows(3)
    hoja.append(["no-existe"])
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    avisos = _avisos(r.json())
    assert len(r.json()["partidas"]) == len(lote["partidas"])
    assert "1 partida no venía en el Excel: quedó como estaba." in avisos
    assert any("no es una partida del lote" in a for a in avisos)


def test_un_excel_de_otro_lote_no_se_carga(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    hoja = libro["_META"]
    for fila in hoja.iter_rows():
        if fila[0].value == "lote_id":
            fila[1].value = "otro-lote"
    assert _subir_version(lote_id, libro, 1).json()["detail"]["errores"][0]["codigo"] == "OTRO_LOTE"
    libro = _bajar(lote_id, 1)
    del libro["_META"]
    hoja = libro["CONCILIACION"]
    for fila in range(2, hoja.max_row + 1):
        hoja[f"A{fila}"] = None
    assert _subir_version(lote_id, libro, 1).json()["detail"]["errores"][0]["codigo"] == "OTRO_LOTE"
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"].delete_cols(1)
    assert _subir_version(lote_id, libro, 1).json()["detail"]["errores"][0]["codigo"] == "SIN_CLAVE"


def test_la_justificacion_puede_ser_texto_libre(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    libro = _bajar(lote["lote"]["id"], 1)
    libro["CONCILIACION"]["P2"] = "Lo habló el cliente con ARCA"
    r = _subir_version(lote["lote"]["id"], libro, 1)
    assert r.status_code == 201, r.text
    assert r.json()["partidas"][0]["justificacion"] == "Lo habló el cliente con ARCA"


def test_lote_repetido_y_anulacion(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    datos = {"task_id": task_id, "empresa": "Piccinini", "cuit_empresa": "30-71111111-1", "periodo": "2026-10"}
    assert cliente.post("/api/lotes", json=datos, headers=ANA).json()["detail"]["codigo"] == "LOTE_REPETIDO"
    base = f"/api/lotes/{lote['lote']['id']}"
    assert cliente.post(base + "/anular", json={"motivo": "corto"}, headers=ANA).status_code == 422
    assert cliente.post(base + "/anular", json={"motivo": "Se cargó el período equivocado"}, headers=ANA).json()["lote"]["estado"] == "anulado"
    assert cliente.post("/api/lotes", json=datos, headers=ANA).status_code == 201


def test_migra_la_bandeja_anterior(tmp_path):
    carpeta = settings.DIRECTORIO_DATOS / "revisiones"
    carpeta.mkdir(parents=True)
    (carpeta / "abc.xlsx").write_bytes(b"PK excel viejo")
    (carpeta / "indice.json").write_text(json.dumps({"abc": {
        "id": "abc", "empresa": "Vieja SA", "periodo": "09/2026", "archivo": "viejo.xlsx", "estado": "observado",
        "enviado_por": "Ana", "revisor": "Luis", "creado": "2026-10-01T10:00:00", "actualizado": None,
        "resumen": {"importe_arca": 10, "importe_mayor": 8}, "controles": {"datos": True},
        "observaciones": [{"id": "o1", "texto": "Falta un asiento", "origen": "nuestro", "autor": "Luis",
                           "fecha": "2026-10-02T10:00:00", "resuelta": False}],
        "historial": [{"fecha": "2026-10-01T10:00:00", "accion": "enviado", "autor": "Ana", "texto": ""},
                      {"fecha": "2026-10-02T10:00:00", "accion": "observacion", "autor": "Luis", "texto": "Falta un asiento"}],
    }}), encoding="utf-8")
    db._iniciadas.clear()
    (lote,) = cliente.get("/api/lotes", headers=ANA).json()["lotes"]
    assert (lote["id"], lote["estado"], lote["maker_nombre"], lote["observaciones_abiertas"]) == ("abc", "rechazado", "Ana", 1)
    assert lote["ultimo_rechazo"] == "Falta un asiento"
    assert (carpeta / "indice.migrado.json").exists() and not (carpeta / "indice.json").exists()


def test_supervisor_se_pasa_un_lote_tomado_por_otro(ruta_arca, ruta_mayor):
    jefa = {"X-Usuario-Id": "jefa", "X-Usuario-Nombre": "Jefa", "X-Usuario-Nivel": "100"}
    lote = _justificar_todo(_lote(ruta_arca, ruta_mayor))
    base = f"/api/lotes/{lote['lote']['id']}"
    cliente.post(base + "/enviar", data={"version": 1}, headers=ANA)
    assert cliente.post(base + "/tomar", headers=LUIS).status_code == 200
    # Un empleado no puede tomar ni quedarse con nada.
    assert cliente.post(base + "/tomar", headers=ANA).status_code == 403
    r = cliente.post(base + "/tomar", headers=jefa)
    assert r.status_code == 200, r.text
    assert r.json()["lote"]["checker_id"] == "jefa"
    eventos = cliente.get(base + "/auditoria", headers=jefa).json()["eventos"]
    assert any(e["action_type"] == "reasignado" for e in eventos)


def test_justificar_en_borrador_no_deja_archivos_sueltos(ruta_arca, ruta_mayor):
    lote = _justificar_todo(_lote(ruta_arca, ruta_mayor))
    lote_id = lote["lote"]["id"]
    clave = [p for p in lote["partidas"] if p["estado"] != "Conciliado Automático"][0]["clave"]
    for texto in ("uno", "dos", "tres"):
        cliente.patch(f"/api/lotes/{lote_id}/partidas/{clave}", json={"version": 1, "comentario": texto}, headers=ANA)
    with sqlite3.connect(db.ruta_db()) as c:
        usados = {s for (s,) in c.execute("SELECT sha256 FROM lote_version UNION SELECT sha256 FROM adjunto")}
    en_disco = {r.name.split(".")[0] for r in db.directorio_archivos().iterdir()}
    assert en_disco == usados


def _en_revision(ruta_arca, ruta_mayor) -> tuple[dict, str]:
    lote = _justificar_todo(_lote(ruta_arca, ruta_mayor))
    base = f"/api/lotes/{lote['lote']['id']}"
    assert cliente.post(base + "/enviar", data={"version": 1}, headers=ANA).status_code == 200
    r = cliente.post(base + "/tomar", headers=LUIS)
    assert r.status_code == 200, r.text
    return r.json(), base


def test_la_diferencia_neta_se_informa_y_se_actualiza_con_el_ajuste(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    assert lote["totales"]["neta"] != "0.00"
    # Las fuentes no cambian: el ajuste va aparte y el cuadre contra ellas sigue dando.
    lote = _justificar_todo(lote)
    assert lote["totales"] == {"sistema": lote["totales"]["declarado"], "declarado": lote["totales"]["declarado"], "neta": "0.00"}
    assert lote["compuertas"]["enviar"] == []
    p = next(x for x in lote["partidas"] if x["estado"] == "No Localizado en ERP")
    assert p["importe_erp"] is None and p["importe_ajustado"] == p["importe_fisco"]

    # Sin el ajuste vuelve a haber diferencia, y queda a la vista. null lo borra.
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "importe_ajustado": None}, headers=ANA).json()
    assert lote["totales"]["neta"] == p["importe_fisco"]
    assert [e["codigo"] for e in lote["pendientes"]] == ["DIFERENCIA_NETA"]

    for malo in ("abc", "-5", "NaN", "Infinity"):
        r = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "importe_ajustado": malo}, headers=ANA)
        assert r.status_code == 422, malo


def test_quitar_una_partida_saca_sus_importes_y_se_puede_restaurar(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    p = next(x for x in lote["partidas"] if x["estado"] == "No Localizado en Fisco")
    assert cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "excluida": True}, headers=ANA).json()["detail"]["codigo"] == "MOTIVO"

    total = lote["totales"]["sistema"]
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "excluida": True, "comentario": "Asiento duplicado"}, headers=ANA).json()
    assert float(lote["totales"]["sistema"]) == float(total) - float(p["importe_erp"])
    # Sigue en la base: sin tipo ni respaldo no falta nada, y el cuadre contra la fuente da.
    quitada = next(x for x in lote["partidas"] if x["clave"] == p["clave"])
    assert quitada["excluida"] == 1 and quitada["faltas"] == []
    assert not [e for e in lote["compuertas"]["enviar"] if e["codigo"].startswith("CUADRE")]

    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "excluida": False}, headers=ANA).json()
    assert lote["totales"]["sistema"] == total


def test_lo_conciliado_solo_no_se_ajusta(ruta_arca, ruta_mayor, tmp_path):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    with sqlite3.connect(db.ruta_db()) as c:
        c.execute("UPDATE partida SET estado = 'Conciliado Automático' WHERE clave = ?", (lote["partidas"][0]["clave"],))
    r = cliente.patch(f"{base}/partidas/{lote['partidas'][0]['clave']}", json={"version": 1, "importe_ajustado": "1.00"}, headers=ANA)
    assert r.json()["detail"]["codigo"] == "CONCILIADA"


def test_el_supervisor_corrige_sin_devolver_y_aprueba(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    clave = lote["partidas"][0]["clave"]
    cliente.put(f"{base}/controles/datos", json={"version": 1, "ok": True}, headers=LUIS)

    # La empleada ya no puede tocarlo, ni otro supervisor que no lo tomó.
    assert cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "x"}, headers=ANA).status_code == 403
    assert cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "x"}, headers=EVA).status_code == 403

    # La corrección arma v2 sin sellar: el lote sigue en revisión y v1 queda como se envió.
    r = cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "Corregido por Luis"}, headers=LUIS)
    assert r.status_code == 200, r.text
    lote = r.json()
    assert (lote["lote"]["estado"], lote["lote"]["version_vigente"], lote["lote"]["checker_id"]) == ("en_revision", 2, "luis")
    assert lote["versiones"][0]["origen"] == "edicion" and lote["versiones"][0]["correccion_checker"] == 1
    assert lote["versiones"][0]["sellada_utc"] is None and lote["versiones"][1]["sellada_utc"]
    assert "luis" not in lote["lote"]["autores"] and lote["controles"]["datos"]["ok"]
    p = next(x for x in lote["partidas"] if x["clave"] == clave)
    assert (p["editado_por"], p["editado_rol"]) == ("Luis", "supervisor")

    # La segunda corrección va sobre la misma versión, y también sube respaldos.
    lote = cliente.patch(f"{base}/partidas/{clave}", json={"version": 2, "comentario": "Otra vez"}, headers=LUIS).json()
    assert lote["lote"]["version_vigente"] == 2
    assert cliente.post(f"{base}/adjuntos", data={"clave_partida": clave}, files={"archivo": ("nc.pdf", b"%PDF luis")}, headers=LUIS).status_code == 201

    for control in cliente.get("/api/lotes/catalogo").json()["controles"]:
        cliente.put(f"{base}/controles/{control}", json={"version": 2, "ok": True}, headers=LUIS)
    r = cliente.post(base + "/aprobar", data={"version": 2}, headers=LUIS)
    assert r.status_code == 200, r.text
    lote = r.json()
    assert lote["lote"]["estado"] == "aprobado" and lote["versiones"][0]["sellada_utc"]


def test_lo_que_corrige_el_supervisor_queda_a_la_vista(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    p = lote["partidas"][0]
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "importe_ajustado": "1.00"}, headers=LUIS).json()
    for control in cliente.get("/api/lotes/catalogo").json()["controles"]:
        cliente.put(f"{base}/controles/{control}", json={"version": 2, "ok": True}, headers=LUIS)
    assert [e["codigo"] for e in cliente.get(base, headers=LUIS).json()["pendientes"]] == ["DIFERENCIA_NETA"]

    # Al devolverlo, la versión queda sellada y lo que sigue es de quien prepara.
    r = cliente.post(base + "/rechazar", json={"version": 2, "motivo": "Revisá el importe que dejé mal"}, headers=LUIS)
    assert r.json()["lote"]["estado"] == "rechazado" and r.json()["versiones"][0]["sellada_utc"]
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 2, "importe_ajustado": p["importe_ajustado"]}, headers=ANA).json()
    assert (lote["lote"]["estado"], lote["lote"]["version_vigente"]) == ("borrador", 3)


def test_el_excel_del_lote_muestra_ajustes_y_exclusiones(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    base = f"/api/lotes/{lote_id}"
    primera, segunda = [p["clave"] for p in lote["partidas"] if p["estado"] != "Conciliado Automático"][:2]
    cliente.patch(f"{base}/partidas/{primera}", json={"version": 1, "importe_ajustado": "10.50"}, headers=ANA)
    cliente.patch(f"{base}/partidas/{segunda}", json={"version": 1, "excluida": True, "comentario": "Duplicada"}, headers=ANA)
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    columnas = {c.value: c.column_letter for c in hoja[1]}
    assert hoja[f"{columnas['Importe ajustado']}2"].value == 10.5 and hoja[f"{columnas['Excluida']}3"].value == "Sí"
    # Y el Excel trabajado afuera no pierde lo corregido en pantalla.
    hoja["Q4"] = "Comentario desde Excel"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    nueva = {p["clave"]: p for p in r.json()["partidas"]}
    assert nueva[primera]["importe_ajustado"] == "10.50" and nueva[segunda]["excluida"] == 1


def test_el_tablero_cuenta_igual_que_el_lote(ruta_arca, ruta_mayor):
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    estado = cliente.get(f"/api/estado/{task_id}").json()
    resumen = estado["resumen"]
    lote = cliente.post("/api/lotes", json={"task_id": task_id, "empresa": "Piccinini", "periodo": resumen["periodo"]}, headers=ANA).json()
    exactas = [p for p in lote["partidas"] if p["estado"] == "Conciliado Automático"]

    assert resumen["periodo"] == "2026-10" and resumen["empresa"] == "" and resumen["cuit_empresa"] == ""
    assert resumen["partidas_exactas"] == len(exactas)
    assert resumen["partidas_a_revisar"] == len(lote["partidas"]) - len(exactas)
    # Los pares que no son exactos quedan marcados, con lo mismo que dirá el lote.
    assert sum(1 for c in estado["detalle"]["conciliados"] if not c["observacion"]) == len(exactas)


def test_quitar_una_fila_baja_el_contador_de_excepciones(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    fila = next(p for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
    antes = cliente.get("/api/lotes", headers=ANA).json()["lotes"][0]["resumen"]["excepciones"]
    r = cliente.patch(f"/api/lotes/{lote['lote']['id']}/partidas/{fila['clave']}",
                      json={"version": lote["lote"]["version_vigente"], "excluida": True, "comentario": "Es de otro período"}, headers=ANA)
    assert r.status_code == 200, r.text
    assert cliente.get("/api/lotes", headers=ANA).json()["lotes"][0]["resumen"]["excepciones"] == antes - 1


# --- Errores del Excel y evidencia -----------------------------------------


def test_un_ajuste_con_letras_se_ignora_y_se_avisa(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["R2"] = "12a4"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    assert _avisos(r.json()) == ["Fila 2: se ignoró el Importe ajustado «12a4», no es un número."]
    assert r.json()["partidas"][0]["importe_ajustado"] is None


def _listo_para_aprobar(ruta_arca, ruta_mayor) -> str:
    return _en_revision(ruta_arca, ruta_mayor)[1]


def _pdf(nombre: str = "comprobante.pdf", cuerpo: bytes = b"%PDF-1.4 evidencia") -> tuple:
    return ("evidencia", (nombre, cuerpo, "application/pdf"))


def test_la_evidencia_viaja_con_el_envio(ruta_arca, ruta_mayor):
    lote = _justificar_todo(_lote(ruta_arca, ruta_mayor))
    base = f"/api/lotes/{lote['lote']['id']}"
    previos = len(lote["adjuntos"])
    r = cliente.post(base + "/enviar", data={"version": 1, "comentario": "Va con NC"}, files=[_pdf(), _pdf("foto.jpg", b"\xff\xd8jpg")], headers=ANA)
    assert r.status_code == 200, r.text
    nuevos = r.json()["adjuntos"][previos:]
    assert [(a["nombre"], a["clave_partida"], a["autor_nombre"]) for a in nuevos] == [("comprobante.pdf", None, "Ana"), ("foto.jpg", None, "Ana")]


def test_la_evidencia_queda_dentro_del_sello_de_aprobacion(ruta_arca, ruta_mayor):
    base = _listo_para_aprobar(ruta_arca, ruta_mayor)
    r = cliente.post(base + "/aprobar", data={"version": 1}, files=[_pdf()], headers=LUIS)
    assert r.status_code == 200, r.text
    lote = r.json()
    assert lote["lote"]["estado"] == "aprobado" and lote["adjuntos"][-1]["autor_nombre"] == "Luis"
    with db.transaccion(escritura=False) as c:
        fila = dict(c.execute("SELECT * FROM lote WHERE id = ?", (lote["lote"]["id"],)).fetchone())
        assert lotes._hash_aprobado(c, fila, lotes._version_vigente(c, fila)) == lote["lote"]["hash_aprobado"]
    eventos = cliente.get(f"{base}/auditoria", headers=LUIS).json()["eventos"]
    subido = next(e for e in eventos if e["action_type"] == "adjunto_subido" and e["detalle"].get("origen") == "evidencia")
    assert subido["user_nombre"] == "Luis" and subido["detalle"]["sha256"] == lote["adjuntos"][-1]["sha256"]


def test_si_no_se_aprueba_no_queda_ninguna_evidencia(ruta_arca, ruta_mayor, monkeypatch):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    previos = len(lote["adjuntos"])
    clave = next(p["clave"] for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
    monkeypatch.setattr(lotes, "compuertas", lambda c, lote: [{"codigo": "SIN_PARTIDAS", "mensaje": "No se puede."}])
    assert cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "importe_ajustado": "1.00"}, headers=LUIS).status_code == 200
    r = cliente.post(base + "/aprobar", data={"version": 2}, files=[_pdf()], headers=LUIS)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "COMPUERTA"
    assert len(cliente.get(base, headers=LUIS).json()["adjuntos"]) == previos


@pytest.mark.parametrize(("nombre", "cuerpo", "estado", "codigo"), [
    ("planilla.xlsx", b"PK", 422, "TIPO_NO_PERMITIDO"),
    ("vacio.pdf", b"", 422, "VACIO"),
    ("grande.pdf", b"x" * (5 * 1024 * 1024 + 1), 413, "ARCHIVO_GRANDE"),
])
def test_evidencia_invalida_frena_la_aprobacion(ruta_arca, ruta_mayor, nombre, cuerpo, estado, codigo):
    base = _listo_para_aprobar(ruta_arca, ruta_mayor)
    r = cliente.post(base + "/aprobar", data={"version": 1}, files=[_pdf(), _pdf(nombre, cuerpo)], headers=LUIS)
    assert r.status_code == estado and r.json()["detail"]["codigo"] == codigo
    assert cliente.get(base, headers=LUIS).json()["lote"]["estado"] == "en_revision"


# --- Todo el trabajo por Excel ---------------------------------------------


def _trabajar_excel(libro, comentario: str = "Ver nota de crédito") -> None:
    """Completa cada excepción en el Excel y lleva la diferencia neta a cero."""
    hoja = libro["CONCILIACION"]
    for fila in range(2, hoja.max_row + 1):
        estado = hoja[f"B{fila}"].value
        if not estado or estado == "Conciliado Automático":
            continue
        hoja[f"P{fila}"] = "Diferencia temporal (se registra en otro período)"
        hoja[f"Q{fila}"] = comentario
        hoja[f"R{fila}"] = hoja[f"M{fila}"].value or 0


def test_el_excel_alcanza_para_dejar_el_lote_listo(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    libro = _bajar(lote["lote"]["id"], 1)
    _trabajar_excel(libro)
    r = _subir_version(lote["lote"]["id"], libro, 1)
    assert r.status_code == 201, r.text
    lote = r.json()
    assert lote["totales"]["neta"] == "0.00"
    assert [e["codigo"] for e in lote["pendientes"]] == ["SIN_RESPALDO"]
    assert all(p["editado_por"] == "Ana" for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
    assert cliente.post(base + "/enviar", data={"version": 2}, files=[_pdf()], headers=ANA).status_code == 200
    assert cliente.post(base + "/tomar", headers=LUIS).status_code == 200
    r = cliente.post(base + "/aprobar", data={"version": 2}, headers=LUIS)
    assert r.status_code == 200 and r.json()["lote"]["estado"] == "aprobado"


def test_excluir_una_fila_desde_el_excel(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    excepcion = next(p for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
    fila = lote["partidas"].index(excepcion) + 2
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"][f"S{fila}"] = "Sí"
    libro["CONCILIACION"][f"Q{fila}"] = "Asiento duplicado"
    lote = _subir_version(lote_id, libro, 1).json()
    assert next(p for p in lote["partidas"] if p["clave"] == excepcion["clave"])["excluida"] == 1


def test_excluida_sin_motivo_se_carga_y_se_pide_al_enviar(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    libro = _bajar(lote["lote"]["id"], 1)
    libro["CONCILIACION"]["S2"] = "x"
    r = _subir_version(lote["lote"]["id"], libro, 1)
    assert r.status_code == 201, r.text
    assert r.json()["partidas"][0]["excluida"] == 1
    assert r.json()["partidas"][0]["faltas"] == ["Falta el motivo por el que se quitó"]


def test_lo_conciliado_solo_tampoco_se_ajusta_por_excel(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    with sqlite3.connect(db.ruta_db()) as c:
        c.execute("UPDATE partida SET estado = 'Conciliado Automático' WHERE clave = ?", (lote["partidas"][0]["clave"],))
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["B2"] = "Conciliado Automático"
    libro["CONCILIACION"]["R2"] = 1
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 201, r.text
    assert r.json()["partidas"][0]["importe_ajustado"] is None
    assert any("concilió sola" in a for a in _avisos(r.json()))


@pytest.mark.parametrize("valor", ["abc", "1-2"])
def test_importe_ajustado_que_no_es_numero(ruta_arca, ruta_mayor, valor):
    lote = _lote(ruta_arca, ruta_mayor)
    fila = next(i for i, p in enumerate(lote["partidas"], start=2) if p["estado"] != "Conciliado Automático")
    libro = _bajar(lote["lote"]["id"], 1)
    libro["CONCILIACION"][f"R{fila}"] = valor
    r = _subir_version(lote["lote"]["id"], libro, 1)
    assert r.status_code == 201, r.text
    assert all(p["importe_ajustado"] is None for p in r.json()["partidas"])


def test_el_excel_subido_se_puede_bajar_y_volver_a_subir(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    _trabajar_excel(libro)
    assert _subir_version(lote_id, libro, 1).status_code == 201
    libro = _bajar(lote_id, 2)
    _trabajar_excel(libro, "Segunda vuelta")
    r = _subir_version(lote_id, libro, 2)
    assert r.status_code == 201, r.text
    assert r.json()["lote"]["version_vigente"] == 3


def test_el_supervisor_sube_un_excel_corregido_sin_devolver(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    libro = _bajar(lote_id, 1)
    _trabajar_excel(libro, "Corregido por Luis")
    salida = io.BytesIO()
    libro.save(salida)
    subir = lambda quien: cliente.post(f"{base}/versiones", data={"version_base": "1"}, files={"archivo": ("x.xlsx", salida.getvalue())}, headers=quien)  # noqa: E731
    assert subir(ANA).status_code == 403
    r = subir(LUIS)
    assert r.status_code == 201, r.text
    lote = r.json()
    assert lote["lote"]["estado"] == "en_revision" and lote["lote"]["checker_id"] == "luis" and "luis" not in lote["lote"]["autores"]
    assert lote["versiones"][0]["correccion_checker"] == 1
    r = cliente.post(base + "/aprobar", data={"version": 2}, headers=LUIS)
    assert r.status_code == 200 and r.json()["lote"]["estado"] == "aprobado"


def _cruce_con_cliente(ruta_arca, ruta_mayor, empresa="Piccinini", cuit="30-71111111-1") -> str:
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    tareas.obtener(task_id).resultado["resumen"].update(empresa=empresa, cuit_empresa=cuit)
    return task_id


def _desde_cruce(task_id: str):
    return cliente.post("/api/lotes/desde-cruce", json={"task_id": task_id}, headers=ANA)


def test_el_cruce_arma_el_lote_y_lo_deja_con_lo_que_falta(ruta_arca, ruta_mayor):
    r = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor))
    assert r.status_code == 201, r.text
    lote = r.json()["lote"]
    assert (lote["estado"], lote["empresa"], lote["periodo"], lote["version_vigente"]) == ("borrador", "Piccinini", "2026-10", 1)


def test_repetir_el_cruce_no_duplica_ni_suma_versiones(ruta_arca, ruta_mayor):
    primero = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor)).json()["lote"]
    segundo = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor)).json()["lote"]
    assert segundo["id"] == primero["id"] and segundo["version_vigente"] == 1


def test_si_cuadra_todo_va_directo_al_supervisor(tmp_path, ruta_mayor):
    lineas = ARCA_CSV.splitlines()
    arca = tmp_path / "solo_perez.csv"
    arca.write_text("\n".join([*lineas[:2], lineas[3]]) + "\n", encoding="cp1252")
    libro = openpyxl.load_workbook(ruta_mayor)
    hoja = libro.active
    for fila in reversed(range(5, hoja.max_row + 1)):
        if "rez" not in str(hoja[f"G{fila}"].value):
            hoja.delete_rows(fila)
    mayor = tmp_path / "solo_perez.xlsx"
    libro.save(mayor)
    r = _desde_cruce(_cruce_con_cliente(str(arca), str(mayor)))
    assert r.status_code == 201, r.text
    detalle = r.json()
    assert detalle["lote"]["estado"] == "pendiente"
    assert all(p["estado"] == "Conciliado Automático" for p in detalle["partidas"])


def test_sin_cliente_no_se_arma_solo(ruta_arca, ruta_mayor):
    r = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor, "", ""))
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "SIN_IDENTIDAD"


def test_un_lote_en_verificacion_no_se_pisa_con_otro_cruce(ruta_arca, ruta_mayor):
    lote = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor)).json()
    lote = _justificar_todo(lote)
    r = cliente.post(f"/api/lotes/{lote['lote']['id']}/enviar", data={"version": lote["lote"]["version_vigente"]}, headers=ANA)
    assert r.status_code == 200, r.text
    r = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor))
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "LOTE_REPETIDO"


def test_solo_importe_y_fecha_sigue_pidiendo_revision():
    par = {"criterio": "importe_fecha", "diferencia_importe": 0, "diferencia_dias": 1}
    assert P._estado_par(par, {"cuit": ""}, {"cuit": ""}, 7) == (P.PENDIENTE, "P01")
    assert P._estado_par({**par, "criterio": "razon_social"}, {"cuit": ""}, {"cuit": ""}, 7) == (P.CONCILIADO, "")


def test_fuera_de_periodo_mira_el_rango_elegido(ruta_arca, ruta_mayor):
    tarea = tareas.obtener(_cruce_con_cliente(ruta_arca, ruta_mayor))
    # «Año en curso»: octubre está adentro, aunque el período termine en noviembre.
    tarea.resultado["resumen"].update(desde_periodo="2026-01-01", hasta_periodo="2026-11-06")
    assert not any("FUERA_PER" in p["flags"] for p in P.armar(tarea.resultado, tarea.entrada))
    tarea.resultado["resumen"].update(desde_periodo="2026-11-01")
    assert all("FUERA_PER" in p["flags"] for p in P.armar(tarea.resultado, tarea.entrada))
    # Una conciliación vieja, sin el inicio: el mes del final, como antes.
    tarea.resultado["resumen"].update(desde_periodo=None, hasta_periodo="2026-11-30")
    assert all("FUERA_PER" in p["flags"] for p in P.armar(tarea.resultado, tarea.entrada))


def test_si_solo_cambian_las_marcas_el_lote_suma_version(ruta_arca, ruta_mayor):
    primero = _desde_cruce(_cruce_con_cliente(ruta_arca, ruta_mayor)).json()["lote"]
    task_id = _cruce_con_cliente(ruta_arca, ruta_mayor)
    tareas.obtener(task_id).resultado["resumen"].update(desde_periodo="2026-11-01", hasta_periodo="2026-11-06")
    segundo = _desde_cruce(task_id).json()
    assert segundo["lote"]["id"] == primero["id"] and segundo["lote"]["version_vigente"] == 2
    assert all("FUERA_PER" in p["flags"] for p in segundo["partidas"])


def test_sin_completar_nada_se_envia_se_aprueba_y_queda_constancia(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    assert lote["totales"]["neta"] != "0.00" and lote["pendientes"]
    r = cliente.post(base + "/enviar", data={"version": 1}, headers=ANA)
    assert r.status_code == 200, r.text
    assert cliente.post(base + "/tomar", headers=LUIS).status_code == 200
    r = cliente.post(base + "/aprobar", data={"version": 1}, headers=LUIS)
    assert r.status_code == 200, r.text
    assert r.json()["lote"]["estado"] == "aprobado" and r.json()["pendientes"] == []
    aprobado = next(e for e in cliente.get(f"{base}/auditoria", headers=LUIS).json()["eventos"] if e["action_type"] == "aprobado")
    assert any("diferencia neta" in m for m in aprobado["detalle"]["pendientes"])
    # Lo aprobado se exporta igual.
    assert cliente.post(f"{base}/exportaciones", json={"tipo": "ddjj_txt"}, headers=ANA).status_code == 201


def test_la_bandeja_dice_quien_subio_y_que_falta(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    excepciones = sum(p["estado"] != "Conciliado Automático" for p in lote["partidas"])
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["P2"] = "Diferencia temporal (se registra en otro período)"
    libro["CONCILIACION"]["Q2"] = "Se registra en noviembre"
    assert _subir_version(lote_id, libro, 1).status_code == 201
    base = f"/api/lotes/{lote_id}"
    assert cliente.post(base + "/enviar", data={"version": 2}, files=[_pdf()], headers=ANA).status_code == 200
    fila = next(x for x in cliente.get("/api/lotes", headers=LUIS).json()["lotes"] if x["id"] == lote_id)
    assert (fila["autor_version"], fila["origen_version"], fila["version_correccion"]) == ("Ana", "subido", 0)
    assert fila["sin_completar"] == excepciones - 1 and fila["comprobantes"] == 1
    assert fila["ultimo_envio"] and fila["version_utc"]


def _triggers() -> set[str]:
    with sqlite3.connect(db.ruta_db()) as c:
        return {f[0] for f in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}


def _aprobado(ruta_arca, ruta_mayor) -> tuple[str, str]:
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    r = cliente.post(base + "/aprobar", data={"version": lote["lote"]["version_vigente"]}, headers=LUIS)
    assert r.status_code == 200, r.text
    cliente.post(f"{base}/exportaciones", json={"tipo": "ddjj_txt"}, headers=LUIS)
    return lote["lote"]["id"], base


def test_eliminar_un_lote_cerrado_lo_borra_y_deja_constancia(ruta_arca, ruta_mayor):
    lote_id, base = _aprobado(ruta_arca, ruta_mayor)
    antes = _triggers()
    archivos = len(list(db.directorio_archivos().iterdir()))
    assert cliente.delete(base, headers=ANA).status_code == 403
    assert cliente.delete(base, headers=LUIS).status_code == 204
    assert cliente.get(base, headers=LUIS).status_code == 404
    assert lote_id not in [x["id"] for x in cliente.get("/api/lotes", headers=LUIS).json()["lotes"]]
    with sqlite3.connect(db.ruta_db()) as c:
        for tabla in ("notificacion", "observacion", "control_revision", "decision", "exportacion_fiscal", "adjunto", "partida", "lote_version"):
            assert c.execute(f"SELECT COUNT(*) FROM {tabla} WHERE lote_id = ?", (lote_id,)).fetchone()[0] == 0, tabla
        assert c.execute("SELECT COUNT(*) FROM auditoria WHERE lote_id = ? AND action_type = 'lote_eliminado'", (lote_id,)).fetchone()[0] == 1
    # Las protecciones siguen en pie, los archivos se fueron y la auditoría no se rompió.
    assert _triggers() == antes
    assert len(list(db.directorio_archivos().iterdir())) < archivos
    assert cliente.get("/api/auditoria/verificar", headers=ADMIN).json()["ok"]


def test_solo_se_eliminan_los_cerrados(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    assert cliente.delete(base, headers=LUIS).status_code == 409
    assert cliente.get(base, headers=LUIS).status_code == 200


def test_un_lote_anulado_tambien_se_elimina(ruta_arca, ruta_mayor):
    base = f"/api/lotes/{_lote(ruta_arca, ruta_mayor)['lote']['id']}"
    assert cliente.post(base + "/anular", json={"motivo": "Era una prueba del sistema"}, headers=ANA).status_code == 200
    assert cliente.delete(base, headers=LUIS).status_code == 204


def test_si_eliminar_falla_el_lote_y_las_protecciones_quedan(ruta_arca, ruta_mayor, monkeypatch):
    lote_id, base = _aprobado(ruta_arca, ruta_mayor)
    antes = _triggers()

    def falla(*_, **__):
        raise RuntimeError("se cortó")

    with monkeypatch.context() as parche:
        parche.setattr(lotes.auditoria, "registrar", falla)
        with pytest.raises(RuntimeError):
            lotes.eliminar(lotes.Usuario("luis", "Luis", frozenset({"supervisor"}), "127.0.0.1", "prueba"), lote_id)
    assert _triggers() == antes
    assert cliente.get(base, headers=LUIS).json()["lote"]["estado"] == "aprobado"


def test_eliminar_se_puede_apagar(ruta_arca, ruta_mayor, monkeypatch):
    monkeypatch.setattr(settings, "PERMITIR_ELIMINAR_CERRADOS", False)
    _, base = _aprobado(ruta_arca, ruta_mayor)
    r = cliente.delete(base, headers=LUIS)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "ELIMINAR_DESACTIVADO"
    assert cliente.get("/api/lotes/catalogo").json()["eliminar_cerrados"] is False


def test_un_empleado_solo_ve_y_toca_sus_lotes(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)  # lo prepara Ana
    lote_id = lote["lote"]["id"]
    base = f"/api/lotes/{lote_id}"
    beto = {"X-Usuario-Id": "beto", "X-Usuario-Nombre": "Beto", "X-Usuario-Nivel": "10", "X-Usuario-Modulos": "Impuestos/Retenciones"}

    # Ana (quien lo preparó) y cualquier supervisor lo ven.
    assert cliente.get(base, headers=ANA).status_code == 200
    assert cliente.get(base, headers=LUIS).status_code == 200
    assert [l["id"] for l in cliente.get("/api/lotes", headers=ANA).json()["lotes"]] == [lote_id]

    # Otro empleado no lo ve en la bandeja ni llega a él por la dirección.
    assert cliente.get("/api/lotes", headers=beto).json()["lotes"] == []
    clave = lote["partidas"][0]["clave"]
    pedidos = [
        cliente.get(base, headers=beto),
        cliente.get(base + "/versiones/1/excel", headers=beto),
        cliente.get(base + "/auditoria", headers=beto),
        cliente.get(base + "/diff", params={"desde": 1, "hasta": 1}, headers=beto),
        cliente.patch(f"{base}/partidas/{clave}", json={"version": 1, "comentario": "x"}, headers=beto),
        cliente.post(base + "/enviar", data={"version": 1}, headers=beto),
        cliente.post(base + "/anular", json={"motivo": "no es mío pero lo anulo"}, headers=beto),
    ]
    assert [r.status_code for r in pedidos] == [403] * len(pedidos)
    assert cliente.get(base, headers=ANA).json()["lote"]["estado"] == "borrador"

    # Un lote que no existe sigue dando 404, no 403.
    assert cliente.get("/api/lotes/no-existe", headers=beto).status_code == 404
