import io
import json
import sqlite3

import openpyxl
import pytest

from app.core import db
from app.core.config import settings
from app.services import excel_lote, lotes
from tests.test_api import _subir, cliente

ANA = {"X-Usuario-Id": "ana", "X-Usuario-Nombre": "Ana", "X-Usuario-Roles": "empleado"}
LUIS = {"X-Usuario-Id": "luis", "X-Usuario-Nombre": "Luis", "X-Usuario-Roles": "supervisor"}
# Supervisora que preparó el lote: igual no lo puede revisar.
EVA = {"X-Usuario-Id": "eva", "X-Usuario-Nombre": "Eva", "X-Usuario-Roles": "supervisor"}
ADMIN = {"X-Usuario-Id": "root", "X-Usuario-Nombre": "Admin", "X-Usuario-Roles": "supervisor"}


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


@pytest.mark.login_real
def test_sin_sesion_no_entra():
    for ruta in ("/api/lotes", "/api/conexiones", "/api/estado/x", "/api/usuarios"):
        r = cliente.get(ruta)
        assert r.status_code == 401 and r.json()["detail"]["codigo"] == "SIN_SESION", ruta
    # Los encabezados de antes ya no alcanzan para entrar.
    r = cliente.get("/api/lotes", headers={"X-Usuario-Id": "ana", "X-Usuario-Roles": "supervisor"})
    assert r.status_code == 401


def test_partidas_con_estado_y_excel_con_hojas_del_lote(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    assert lote["lote"]["estado"] == "borrador" and lote["lote"]["version_vigente"] == 1
    estados = sorted((p["estado"], p["codigo"]) for p in lote["partidas"])
    # BANCO: par por certificado pero a 12 días (P03); PEREZ: por razón social, sin certificado (P01);
    # PROVEEDOR: dos sin registrar (N01); el asiento de IVA de 1.500: no está en ARCA (N02).
    assert estados == [("No Localizado en ERP", "N01"), ("No Localizado en ERP", "N01"), ("No Localizado en Fisco", "N02"),
                       ("Pendiente de Comprobante", "P01"), ("Pendiente de Comprobante", "P03")]
    # El cuadre al centavo contra las fuentes da.
    assert lote["versiones"][0]["validacion"]["valida"]
    assert not [e for e in lote["compuertas"]["enviar"] if e["codigo"].startswith("CUADRE")]
    # Sin justificar no se puede enviar.
    assert {e["codigo"] for e in lote["compuertas"]["enviar"]} == {"SIN_JUSTIFICACION", "SIN_COMENTARIO", "SIN_RESPALDO", "DIFERENCIA_NETA"}

    excel = cliente.get(f"/api/lotes/{lote['lote']['id']}/versiones/1/excel", headers=ANA)
    libro = openpyxl.load_workbook(io.BytesIO(excel.content))
    assert libro.sheetnames == ["CONCILIACION", "ARCA", "LIBRO MAYOR", "_META"]
    assert libro["_META"].sheet_state == "veryHidden" and libro["CONCILIACION"].protection.sheet
    assert [c.value for c in libro["CONCILIACION"][1]] == excel_lote.ENCABEZADOS


def test_ciclo_completo_maker_checker(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    base = f"/api/lotes/{lote_id}"

    # Un empleado no revisa.
    assert cliente.post(base + "/tomar", headers=ANA).status_code == 403
    assert cliente.post(base + "/enviar", data={"version": 1}, headers=ANA).json()["detail"]["codigo"] == "COMPUERTA"

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


def test_respaldo_obligatorio(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    for p in lote["partidas"]:
        lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "justificacion": "error_carga", "comentario": "Ver NC"},
                             headers=ANA).json()
    assert {e["codigo"] for e in lote["compuertas"]["enviar"]} == {"SIN_RESPALDO", "DIFERENCIA_NETA"}
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


def test_subida_que_altera_columnas_bloqueadas(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    libro = _bajar(lote_id, 1)
    hoja = libro["CONCILIACION"]
    hoja["M2"] = 1.0  # Importe Fisco
    hoja["Q3"] = "=1+1"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 422
    errores = r.json()["detail"]["errores"]
    assert {(e["codigo"], e["fila"]) for e in errores} == {("COLUMNA_ALTERADA", 2), ("FORMULA", 3)}
    # Sobre una versión vieja o del mismo archivo, tampoco.
    assert _subir_version(lote_id, _bajar(lote_id, 1), 7).json()["detail"]["codigo"] == "VERSION_VIEJA"
    original = cliente.get(f"/api/lotes/{lote_id}/versiones/1/excel", headers=ANA).content
    r = cliente.post(f"/api/lotes/{lote_id}/versiones", data={"version_base": "1"},
                     files={"archivo": ("x.xlsx", original)}, headers=ANA)
    assert r.json()["detail"]["errores"][0]["codigo"] == "SIN_CAMBIOS"
    acciones = [e["action_type"] for e in cliente.get(f"/api/lotes/{lote_id}/auditoria", headers=ANA).json()["eventos"]]
    assert acciones.count("carga_rechazada") == 2


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
    jefa = {"X-Usuario-Id": "jefa", "X-Usuario-Nombre": "Jefa", "X-Usuario-Roles": "supervisor"}
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


def test_la_diferencia_neta_bloquea_y_se_actualiza_con_el_ajuste(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    base = f"/api/lotes/{lote['lote']['id']}"
    assert lote["totales"]["neta"] != "0.00"
    # Las fuentes no cambian: el ajuste va aparte y el cuadre contra ellas sigue dando.
    lote = _justificar_todo(lote)
    assert lote["totales"] == {"sistema": lote["totales"]["declarado"], "declarado": lote["totales"]["declarado"], "neta": "0.00"}
    assert lote["compuertas"]["enviar"] == []
    p = next(x for x in lote["partidas"] if x["estado"] == "No Localizado en ERP")
    assert p["importe_erp"] is None and p["importe_ajustado"] == p["importe_fisco"]

    # Sin el ajuste vuelve a haber diferencia y no se puede enviar. null lo borra.
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "importe_ajustado": None}, headers=ANA).json()
    assert lote["totales"]["neta"] == p["importe_fisco"]
    assert [e["codigo"] for e in lote["compuertas"]["enviar"]] == ["DIFERENCIA_NETA"]
    assert cliente.post(base + "/enviar", data={"version": 1}, headers=ANA).json()["detail"]["codigo"] == "COMPUERTA"

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


def test_lo_que_corrige_el_supervisor_tambien_pasa_por_las_compuertas(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    p = lote["partidas"][0]
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 1, "importe_ajustado": "1.00"}, headers=LUIS).json()
    for control in cliente.get("/api/lotes/catalogo").json()["controles"]:
        cliente.put(f"{base}/controles/{control}", json={"version": 2, "ok": True}, headers=LUIS)
    r = cliente.post(base + "/aprobar", data={"version": 2}, headers=LUIS)
    assert r.status_code == 409 and [e["codigo"] for e in r.json()["detail"]["errores"]] == ["DIFERENCIA_NETA"]

    # Al devolverlo, la versión queda sellada y lo que sigue es de quien prepara.
    r = cliente.post(base + "/rechazar", json={"version": 2, "motivo": "Revisá el importe que dejé mal"}, headers=LUIS)
    assert r.json()["lote"]["estado"] == "rechazado" and r.json()["versiones"][0]["sellada_utc"]
    lote = cliente.patch(f"{base}/partidas/{p['clave']}", json={"version": 2, "importe_ajustado": p["importe_ajustado"]}, headers=ANA).json()
    assert (lote["lote"]["estado"], lote["lote"]["version_vigente"]) == ("borrador", 3)


def test_el_excel_del_lote_muestra_ajustes_y_exclusiones(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    base = f"/api/lotes/{lote_id}"
    primera, segunda = lote["partidas"][0]["clave"], lote["partidas"][1]["clave"]
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


def test_letras_en_un_importe_se_informan_con_columna_y_fila(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["M2"] = "12a4"
    r = _subir_version(lote_id, libro, 1)
    assert r.status_code == 422
    detalle = r.json()["detail"]
    assert detalle["errores"][0]["codigo"] == "IMPORTE_INVALIDO"
    assert detalle["errores"][0]["mensaje"] == "Error de formato: la columna «Importe Fisco» en la fila 2 contiene letras («12a4»)."
    assert "etapa" not in detalle["mensaje"]


def test_columna_eliminada_se_nombra(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"].delete_cols(9)  # Certificado
    errores = _subir_version(lote_id, libro, 1).json()["detail"]["errores"]
    assert [e["mensaje"] for e in errores] == ["Se eliminó la columna original «Certificado»."]


def test_cuit_modificada_dice_que_cambio(ruta_arca, ruta_mayor):
    lote_id = _lote(ruta_arca, ruta_mayor)["lote"]["id"]
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["F2"] = "20-99999999-9"
    error = _subir_version(lote_id, libro, 1).json()["detail"]["errores"][0]
    assert error["codigo"] == "COLUMNA_ALTERADA" and error["columna"] == "CUIT"
    assert "en la fila 2" in error["mensaje"] and "20-99999999-9" in error["mensaje"]


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


def test_si_no_se_aprueba_no_queda_ninguna_evidencia(ruta_arca, ruta_mayor):
    lote, base = _en_revision(ruta_arca, ruta_mayor)
    previos = len(lote["adjuntos"])
    clave = next(p["clave"] for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
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
    assert [e["codigo"] for e in lote["compuertas"]["enviar"]] == ["SIN_RESPALDO"]
    assert all(p["editado_por"] == "Ana" for p in lote["partidas"] if p["estado"] != "Conciliado Automático")
    # El comprobante que llega con el envío cuenta para la compuerta.
    assert cliente.post(base + "/enviar", data={"version": 2}, headers=ANA).json()["detail"]["codigo"] == "COMPUERTA"
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
    errores = _subir_version(lote_id, libro, 1).json()["detail"]["errores"]
    assert [e["codigo"] for e in errores] == ["MOTIVO"]
    libro["CONCILIACION"][f"Q{fila}"] = "Asiento duplicado"
    lote = _subir_version(lote_id, libro, 1).json()
    assert next(p for p in lote["partidas"] if p["clave"] == excepcion["clave"])["excluida"] == 1


def test_lo_conciliado_solo_tampoco_se_ajusta_por_excel(ruta_arca, ruta_mayor):
    lote = _lote(ruta_arca, ruta_mayor)
    lote_id = lote["lote"]["id"]
    with sqlite3.connect(db.ruta_db()) as c:
        c.execute("UPDATE partida SET estado = 'Conciliado Automático' WHERE clave = ?", (lote["partidas"][0]["clave"],))
    libro = _bajar(lote_id, 1)
    libro["CONCILIACION"]["B2"] = "Conciliado Automático"
    libro["CONCILIACION"]["R2"] = 1
    assert _subir_version(lote_id, libro, 1).json()["detail"]["errores"][0]["codigo"] == "CONCILIADA"


@pytest.mark.parametrize(("valor", "texto"), [("abc", "contiene letras"), ("1-2", "no tiene un importe válido"), (-5, "no puede ser negativa")])
def test_importe_ajustado_invalido(ruta_arca, ruta_mayor, valor, texto):
    lote = _lote(ruta_arca, ruta_mayor)
    fila = next(i for i, p in enumerate(lote["partidas"], start=2) if p["estado"] != "Conciliado Automático")
    libro = _bajar(lote["lote"]["id"], 1)
    libro["CONCILIACION"][f"R{fila}"] = valor
    error = _subir_version(lote["lote"]["id"], libro, 1).json()["detail"]["errores"][0]
    assert error["codigo"] == "IMPORTE_INVALIDO" and texto in error["mensaje"]


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
