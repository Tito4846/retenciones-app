"""
Migración única de la bandeja anterior (DIRECTORIO_DATOS/revisiones/indice.json)
a lotes en SQLite. Corre sola la primera vez que se abre la base; después
renombra el índice a indice.migrado.json para no repetirla.

Los envíos viejos no tienen la hoja CONCILIACION: quedan con su Excel como v1
sin partidas. Para seguir trabajando uno que no estaba aprobado hay que
regenerarlo desde una conciliación nueva.
"""

import hashlib
import json
import logging
import sqlite3
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.usuarios import Usuario

logger = logging.getLogger(__name__)

_ESTADOS = {"pendiente": "pendiente", "en_revision": "en_revision", "observado": "rechazado", "aprobado": "aprobado"}
_CONTROLES = {"faltan_en_mayor": "faltan_en_erp", "faltan_en_arca": "faltan_en_fisco", "diferencia": "diferencias", "datos": "datos"}


def _utc(fecha: str | None) -> str:
    # Las fechas viejas son hora local sin zona: se guardan tal cual, marcadas.
    return (fecha or "") + ("" if not fecha or fecha.endswith("Z") else "-03:00")


def migrar_revisiones_json(c: sqlite3.Connection) -> None:
    carpeta = Path(settings.DIRECTORIO_DATOS) / "revisiones"
    indice = carpeta / "indice.json"
    if not indice.exists():
        return
    from app.services import auditoria
    from app.services.lotes import _guardar_archivo

    revisiones = json.loads(indice.read_text(encoding="utf-8"))
    c.execute("BEGIN IMMEDIATE")
    try:
        for r in revisiones.values():
            if c.execute("SELECT 1 FROM lote WHERE id = ?", (r["id"],)).fetchone():
                continue
            maker = Usuario(r["enviado_por"], r["enviado_por"], frozenset({"empleado"}))
            resumen = r.get("resumen") or {}
            total_erp = f"{float(resumen.get('importe_mayor') or 0):.2f}"
            total_fisco = f"{float(resumen.get('importe_arca') or 0):.2f}"
            creado = _utc(r["creado"])
            c.execute(
                "INSERT INTO lote (id, empresa, periodo, estado, version_vigente, maker_id, maker_nombre, checker_id, checker_nombre, "
                "total_libro_erp, total_fisco, migrado, creado_utc, actualizado_utc) VALUES (?, ?, ?, 'pendiente', 1, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (r["id"], r["empresa"], r.get("periodo") or "", maker.id, maker.nombre, r.get("revisor"), r.get("revisor"),
                 total_erp, total_fisco, creado, _utc(r.get("actualizado") or r["creado"])))
            excel = carpeta / f"{r['id']}.xlsx"
            contenido = excel.read_bytes() if excel.exists() else b""
            sha = _guardar_archivo(contenido, ".xlsx") if contenido else hashlib.sha256(b"").hexdigest()
            version_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO lote_version (id, lote_id, numero, origen, sha256, nombre_archivo, autor_id, autor_nombre, total_libro_erp, "
                "total_fisco, resumen_json, validacion_json, sellada_utc, creado_utc) VALUES (?, ?, 1, 'migrado', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, r["id"], sha, r["archivo"], maker.id, maker.nombre, total_erp, total_fisco, json.dumps(resumen),
                 json.dumps({"valida": True, "migrado": True}), creado, creado))
            for clave, ok in (r.get("controles") or {}).items():
                if clave in _CONTROLES and r.get("revisor"):
                    c.execute("INSERT OR REPLACE INTO control_revision VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (r["id"], version_id, _CONTROLES[clave], int(ok), r["revisor"], r["revisor"], creado))
            for o in r.get("observaciones") or []:
                c.execute("INSERT INTO observacion (id, lote_id, version_id, texto, origen, autor_id, autor_nombre, creado_utc, "
                          "resuelta_por, resuelta_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (o["id"], r["id"], version_id, o["texto"], o["origen"], o["autor"], o["autor"], _utc(o["fecha"]),
                           o["autor"] if o["resuelta"] else None, _utc(o["fecha"]) if o["resuelta"] else None))
            for h in r.get("historial") or []:
                autor = Usuario(h["autor"], h["autor"])
                auditoria.registrar(c, autor, f"migrado:{h['accion']}", r["id"], version_id, fecha_original=h["fecha"], texto=h["texto"])
            estado = _ESTADOS.get(r["estado"], "pendiente")
            if estado == "rechazado":
                motivo = next((h["texto"] for h in reversed(r.get("historial") or []) if h["accion"] in ("observado", "observacion") and h["texto"]),
                              "Devuelto con observaciones (bandeja anterior).")
                c.execute("INSERT INTO decision (id, lote_id, version_id, tipo, motivo, usuario_id, usuario_nombre, fecha_utc) "
                          "VALUES (?, ?, ?, 'rechazo', ?, ?, ?, ?)",
                          (uuid.uuid4().hex[:12], r["id"], version_id, motivo, r.get("revisor") or "", r.get("revisor") or "", creado))
            if estado != "pendiente":
                c.execute("UPDATE lote SET estado = ? WHERE id = ?", (estado, r["id"]))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        logger.exception("No se pudo migrar la bandeja de revisión anterior")
        return
    indice.replace(indice.with_name("indice.migrado.json"))
    logger.info("Bandeja de revisión anterior migrada: %d envíos", len(revisiones))
