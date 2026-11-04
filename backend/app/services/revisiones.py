"""Bandeja de revisión: los papeles de trabajo que se mandan a verificar a mano.

Cada envío guarda el Excel tal como salió y un resumen del cruce, y pasa por
estados: pendiente -> en revisión -> con observaciones / aprobado. Quien revisa
tilda los controles y, si encuentra algo, deja una observación diciendo de
quién es el error (nuestro, del cliente o de ARCA). Se guarda en disco, en
DIRECTORIO_DATOS/revisiones, para que no se pierda al reiniciar el servidor.
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.core.config import settings

ESTADOS = ("pendiente", "en_revision", "observado", "aprobado")
ORIGENES = ("nuestro", "cliente", "arca")

# Lo que se verifica en cada papel de trabajo antes de aprobarlo.
CONTROLES = {
    "faltan_en_mayor": "Partidas de ARCA pendientes de registrar: verificadas una por una",
    "faltan_en_arca": "Partidas contabilizadas sin ARCA: revisada la procedencia / el cliente",
    "diferencia": "Diferencia ARCA − Mayor justificada con los motivos",
    "datos": "Cliente, período e importes del Excel correctos",
}

_lock = threading.Lock()


class ErrorDeRevision(Exception):
    pass


def _directorio() -> Path:
    carpeta = Path(settings.DIRECTORIO_DATOS) / "revisiones"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _indice() -> Path:
    return _directorio() / "indice.json"


def _leer() -> dict[str, dict]:
    ruta = _indice()
    if not ruta.exists():
        return {}
    return json.loads(ruta.read_text(encoding="utf-8"))


def _escribir(datos: dict[str, dict]) -> None:
    ruta = _indice()
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    temporal.replace(ruta)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _evento(revision: dict, accion: str, autor: str, texto: str = "") -> None:
    revision["historial"].append({"fecha": _ahora(), "accion": accion, "autor": autor or "Sin nombre", "texto": texto})
    revision["actualizado"] = revision["historial"][-1]["fecha"]


def crear(contenido: bytes, nombre_archivo: str, resultado: dict, empresa: str, periodo: str,
          enviado_por: str, comentario: str = "") -> dict:
    resumen = resultado.get("resumen", {})
    revision = {
        "id": uuid.uuid4().hex[:12],
        "empresa": empresa.strip() or " ".join(["Conciliación", periodo.strip()]).strip(),
        "periodo": periodo.strip(),
        "archivo": nombre_archivo,
        "estado": "pendiente",
        "enviado_por": enviado_por.strip() or "Sin nombre",
        "revisor": None,
        "creado": _ahora(),
        "actualizado": None,
        "resumen": {clave: resumen.get(clave) for clave in (
            "total_conciliados", "total_faltan_en_arca", "total_faltan_en_mayor", "total_manuales",
            "importe_arca", "importe_mayor", "diferencia", "hasta_periodo")},
        "controles": {clave: False for clave in CONTROLES},
        "observaciones": [],
        "historial": [],
    }
    _evento(revision, "enviado", revision["enviado_por"], comentario.strip())
    with _lock:
        (_directorio() / f"{revision['id']}.xlsx").write_bytes(contenido)
        datos = _leer()
        datos[revision["id"]] = revision
        _escribir(datos)
    return revision


def listar() -> list[dict]:
    with _lock:
        return sorted(_leer().values(), key=lambda r: r["creado"], reverse=True)


def obtener(revision_id: str) -> dict | None:
    with _lock:
        return _leer().get(revision_id)


def ruta_excel(revision_id: str) -> Path:
    return _directorio() / f"{revision_id}.xlsx"


def _modificar(revision_id: str, cambio) -> dict:
    with _lock:
        datos = _leer()
        revision = datos.get(revision_id)
        if revision is None:
            raise KeyError(revision_id)
        cambio(revision)
        _escribir(datos)
        return revision


def cambiar_estado(revision_id: str, estado: str, autor: str, comentario: str = "") -> dict:
    if estado not in ESTADOS:
        raise ErrorDeRevision(f"Estado desconocido: {estado}.")

    def aplicar(r: dict) -> None:
        if estado == "aprobado":
            faltan = [CONTROLES[c] for c, ok in r["controles"].items() if not ok]
            if faltan:
                raise ErrorDeRevision("Para aprobar faltan tildar controles: " + "; ".join(faltan) + ".")
            if any(not o["resuelta"] for o in r["observaciones"]):
                raise ErrorDeRevision("Hay observaciones sin resolver: resolvelas antes de aprobar.")
        if estado == "observado" and not comentario.strip() and all(o["resuelta"] for o in r["observaciones"]):
            raise ErrorDeRevision("Para devolver con observaciones, contá qué hay que corregir.")
        if estado in ("en_revision", "observado", "aprobado"):
            r["revisor"] = autor.strip() or r["revisor"]
        r["estado"] = estado
        _evento(r, estado, autor, comentario.strip())

    return _modificar(revision_id, aplicar)


def marcar_control(revision_id: str, control: str, ok: bool, autor: str) -> dict:
    if control not in CONTROLES:
        raise ErrorDeRevision(f"Control desconocido: {control}.")

    def aplicar(r: dict) -> None:
        if r["estado"] == "aprobado":
            raise ErrorDeRevision("Ya está aprobado: reabrilo para cambiar los controles.")
        r["controles"][control] = ok
        if r["estado"] == "pendiente":
            r["estado"] = "en_revision"
            r["revisor"] = autor.strip() or r["revisor"]
        _evento(r, "control" if ok else "control_quitado", autor, CONTROLES[control])

    return _modificar(revision_id, aplicar)


def observar(revision_id: str, texto: str, origen: str, autor: str) -> dict:
    if not texto.strip():
        raise ErrorDeRevision("Escribí la observación.")
    if origen not in ORIGENES:
        raise ErrorDeRevision(f"Origen desconocido: {origen}.")

    def aplicar(r: dict) -> None:
        r["observaciones"].append({
            "id": uuid.uuid4().hex[:8], "texto": texto.strip(), "origen": origen,
            "autor": autor.strip() or "Sin nombre", "fecha": _ahora(), "resuelta": False,
        })
        if r["estado"] in ("pendiente", "en_revision", "aprobado"):
            r["estado"] = "observado"
            r["revisor"] = autor.strip() or r["revisor"]
        _evento(r, "observacion", autor, texto.strip())

    return _modificar(revision_id, aplicar)


def resolver_observacion(revision_id: str, observacion_id: str, resuelta: bool, autor: str) -> dict:
    def aplicar(r: dict) -> None:
        observacion = next((o for o in r["observaciones"] if o["id"] == observacion_id), None)
        if observacion is None:
            raise ErrorDeRevision("La observación no existe.")
        observacion["resuelta"] = resuelta
        _evento(r, "resuelta" if resuelta else "reabierta", autor, observacion["texto"])

    return _modificar(revision_id, aplicar)


def eliminar(revision_id: str) -> None:
    with _lock:
        datos = _leer()
        if datos.pop(revision_id, None) is None:
            raise KeyError(revision_id)
        _escribir(datos)
        ruta_excel(revision_id).unlink(missing_ok=True)
