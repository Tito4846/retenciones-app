import io

import openpyxl
import pytest

from app.core.config import settings
from tests.test_api import _subir, cliente


@pytest.fixture(autouse=True)
def datos_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DIRECTORIO_DATOS", tmp_path)


def test_circuito_de_revision(ruta_arca, ruta_mayor):
    task_id = _subir(ruta_arca, ruta_mayor).json()["task_id"]
    r = cliente.post(f"/api/revisiones/{task_id}", json={"empresa": "Piccinini", "periodo": "10/2026", "enviado_por": "Ana"})
    assert r.status_code == 201
    revision = r.json()
    assert revision["estado"] == "pendiente" and revision["resumen"]["total_conciliados"] == 2

    # Queda guardado con su Excel.
    (listada,) = cliente.get("/api/revisiones").json()["revisiones"]
    assert listada["id"] == revision["id"]
    excel = cliente.get(f"/api/revisiones/{revision['id']}/excel")
    assert openpyxl.load_workbook(io.BytesIO(excel.content)).sheetnames == ["ARCA", "LIBRO MAYOR"]

    base = f"/api/revisiones/{revision['id']}"
    # Sin los controles tildados no se aprueba.
    assert cliente.post(f"{base}/estado", json={"estado": "aprobado", "autor": "Luis"}).status_code == 409
    # Tildar un control la pasa a "en revisión".
    assert cliente.post(f"{base}/controles/datos", json={"ok": True, "autor": "Luis"}).json()["estado"] == "en_revision"

    # Una observación (error nuestro) la devuelve; resuelta y con todo tildado, se aprueba.
    obs = cliente.post(f"{base}/observaciones", json={"texto": "Falta un asiento", "origen": "nuestro", "autor": "Luis"}).json()
    assert obs["estado"] == "observado"
    for control in ("faltan_en_mayor", "faltan_en_arca", "diferencia"):
        cliente.post(f"{base}/controles/{control}", json={"ok": True, "autor": "Luis"})
    assert cliente.post(f"{base}/estado", json={"estado": "aprobado"}).status_code == 409
    cliente.post(f"{base}/observaciones/{obs['observaciones'][0]['id']}", json={"resuelta": True})
    aprobada = cliente.post(f"{base}/estado", json={"estado": "aprobado", "autor": "Luis"}).json()
    assert aprobada["estado"] == "aprobado" and aprobada["revisor"] == "Luis"
    assert [h["accion"] for h in aprobada["historial"]][0] == "enviado"

    assert cliente.delete(base).status_code == 204
    assert cliente.get("/api/revisiones").json()["revisiones"] == []


def test_enviar_sin_conciliacion():
    assert cliente.post("/api/revisiones/no-existe", json={}).status_code == 404
