"""
Log de auditoría: cada evento queda en una fila que no se puede cambiar ni
borrar (triggers) y encadenada con la anterior por hash. Si alguien edita la
base a mano, `verificar()` encuentra dónde se corta la cadena.
"""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from app.core.usuarios import Usuario

GENESIS = "0" * 64


def ahora_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hash(previo: str, fila: dict) -> str:
    cuerpo = json.dumps(fila, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((previo + cuerpo).encode("utf-8")).hexdigest()


_CAMPOS = ("ts_utc", "user_id", "user_nombre", "roles", "ip_address", "user_agent",
           "action_type", "lote_id", "version_id", "detalle_json")


def registrar(conexion: sqlite3.Connection, usuario: Usuario, accion: str, lote_id: str | None = None,
              version_id: str | None = None, **detalle) -> None:
    """Agrega el evento. Va dentro de la transacción de la operación que registra."""
    fila = {
        "ts_utc": ahora_utc(),
        "user_id": usuario.id,
        "user_nombre": usuario.nombre,
        "roles": ",".join(sorted(usuario.roles)),
        "ip_address": usuario.ip,
        "user_agent": usuario.user_agent,
        "action_type": accion,
        "lote_id": lote_id,
        "version_id": version_id,
        "detalle_json": json.dumps(detalle, ensure_ascii=False, sort_keys=True, default=str),
    }
    ultima = conexion.execute("SELECT hash FROM auditoria ORDER BY id DESC LIMIT 1").fetchone()
    previo = ultima["hash"] if ultima else GENESIS
    conexion.execute(
        f"INSERT INTO auditoria ({', '.join(_CAMPOS)}, hash_previo, hash) VALUES ({', '.join('?' * (len(_CAMPOS) + 2))})",
        [*(fila[c] for c in _CAMPOS), previo, _hash(previo, fila)],
    )


def _a_dict(fila: sqlite3.Row) -> dict:
    datos = dict(fila)
    datos["detalle"] = json.loads(datos.pop("detalle_json") or "{}")
    return datos


def listar(conexion: sqlite3.Connection, lote_id: str | None = None, usuario: str | None = None,
           accion: str | None = None, desde: str | None = None, hasta: str | None = None,
           limite: int = 500) -> list[dict]:
    condiciones, parametros = [], []
    for campo, valor, operador in (("lote_id", lote_id, "="), ("user_id", usuario, "="), ("action_type", accion, "="),
                                   ("ts_utc", desde, ">="), ("ts_utc", hasta, "<=")):
        if valor:
            condiciones.append(f"{campo} {operador} ?")
            parametros.append(valor)
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    filas = conexion.execute(f"SELECT * FROM auditoria {donde} ORDER BY id DESC LIMIT ?", [*parametros, limite])
    return [_a_dict(f) for f in filas]


def verificar(conexion: sqlite3.Connection) -> dict:
    """Recorre la cadena entera. `ok` False y el primer id roto si alguien la tocó."""
    previo, cantidad = GENESIS, 0
    for fila in conexion.execute("SELECT * FROM auditoria ORDER BY id"):
        datos = {c: fila[c] for c in _CAMPOS}
        if fila["hash_previo"] != previo or fila["hash"] != _hash(previo, datos):
            return {"ok": False, "eventos": cantidad, "roto_en": fila["id"]}
        previo, cantidad = fila["hash"], cantidad + 1
    return {"ok": True, "eventos": cantidad, "roto_en": None}
