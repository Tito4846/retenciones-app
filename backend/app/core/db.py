"""
Base SQLite del módulo de revisión (lotes, versiones, partidas, adjuntos,
auditoría). Vive en DIRECTORIO_DATOS/retenciones.db: un archivo, sin servidor.

Cada operación abre su conexión (`transaccion()`), así sirve igual desde los
hilos de uvicorn. Las escrituras van en BEGIN IMMEDIATE: una sola por vez.

La inmutabilidad no depende solo de la API: los triggers abortan cualquier
cambio sobre un lote aprobado, sobre una versión sellada (ya enviada) y sobre
la auditoría.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lote (
    id TEXT PRIMARY KEY,
    empresa TEXT NOT NULL,
    cuit_empresa TEXT NOT NULL DEFAULT '',
    periodo TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL CHECK (estado IN ('borrador','pendiente','en_revision','rechazado','aprobado','anulado')),
    version_vigente INTEGER NOT NULL DEFAULT 0,
    maker_id TEXT NOT NULL,
    maker_nombre TEXT NOT NULL,
    checker_id TEXT,
    checker_nombre TEXT,
    total_libro_erp TEXT NOT NULL DEFAULT '0.00',
    total_fisco TEXT NOT NULL DEFAULT '0.00',
    hash_aprobado TEXT,
    rectifica_a TEXT REFERENCES lote(id),
    migrado INTEGER NOT NULL DEFAULT 0,
    creado_utc TEXT NOT NULL,
    actualizado_utc TEXT NOT NULL,
    aprobado_utc TEXT
);

CREATE TABLE IF NOT EXISTS lote_version (
    id TEXT PRIMARY KEY,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    numero INTEGER NOT NULL,
    origen TEXT NOT NULL CHECK (origen IN ('generado','subido','edicion','migrado')),
    sha256 TEXT NOT NULL,
    nombre_archivo TEXT NOT NULL,
    autor_id TEXT NOT NULL,
    autor_nombre TEXT NOT NULL,
    total_libro_erp TEXT NOT NULL DEFAULT '0.00',
    total_fisco TEXT NOT NULL DEFAULT '0.00',
    resumen_json TEXT NOT NULL DEFAULT '{}',
    validacion_json TEXT NOT NULL DEFAULT '{}',
    sellada_utc TEXT,
    creado_utc TEXT NOT NULL,
    -- 1 = la armó el supervisor que revisa, corrigiendo sin devolver el lote.
    correccion_checker INTEGER NOT NULL DEFAULT 0,
    UNIQUE (lote_id, numero)
);

CREATE TABLE IF NOT EXISTS partida (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    version_id TEXT NOT NULL REFERENCES lote_version(id),
    orden INTEGER NOT NULL,
    clave TEXT NOT NULL,
    estado TEXT NOT NULL,
    codigo TEXT NOT NULL DEFAULT '',
    flags TEXT NOT NULL DEFAULT '[]',
    criterio TEXT NOT NULL DEFAULT '',
    cuit TEXT NOT NULL DEFAULT '',
    razon_social TEXT NOT NULL DEFAULT '',
    impuesto TEXT NOT NULL DEFAULT '',
    certificado TEXT NOT NULL DEFAULT '',
    documento_erp TEXT NOT NULL DEFAULT '',
    fecha_fisco TEXT,
    fecha_erp TEXT,
    importe_fisco TEXT,
    importe_erp TEXT,
    diferencia TEXT NOT NULL DEFAULT '0.00',
    justificacion TEXT NOT NULL DEFAULT '',
    comentario TEXT NOT NULL DEFAULT '',
    hash_fila TEXT NOT NULL,
    -- Lo que se corrige va aparte: importe_fisco e importe_erp son las fuentes y no se tocan.
    importe_ajustado TEXT,
    excluida INTEGER NOT NULL DEFAULT 0,
    editado_por TEXT NOT NULL DEFAULT '',
    editado_rol TEXT NOT NULL DEFAULT '',
    editado_utc TEXT,
    UNIQUE (version_id, clave)
);

CREATE TABLE IF NOT EXISTS adjunto (
    id TEXT PRIMARY KEY,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    clave_partida TEXT,
    version_id TEXT,
    nombre TEXT NOT NULL,
    mime TEXT NOT NULL,
    tamano INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    autor_id TEXT NOT NULL,
    autor_nombre TEXT NOT NULL,
    creado_utc TEXT NOT NULL,
    retirado_utc TEXT,
    retirado_por TEXT
);

CREATE TABLE IF NOT EXISTS observacion (
    id TEXT PRIMARY KEY,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    version_id TEXT NOT NULL,
    clave_partida TEXT,
    texto TEXT NOT NULL,
    origen TEXT NOT NULL CHECK (origen IN ('nuestro','cliente','arca')),
    autor_id TEXT NOT NULL,
    autor_nombre TEXT NOT NULL,
    creado_utc TEXT NOT NULL,
    resuelta_por TEXT,
    resuelta_utc TEXT
);

CREATE TABLE IF NOT EXISTS control_revision (
    lote_id TEXT NOT NULL REFERENCES lote(id),
    version_id TEXT NOT NULL,
    control TEXT NOT NULL,
    ok INTEGER NOT NULL,
    usuario_id TEXT NOT NULL,
    usuario_nombre TEXT NOT NULL,
    fecha_utc TEXT NOT NULL,
    PRIMARY KEY (lote_id, version_id, control)
);

CREATE TABLE IF NOT EXISTS decision (
    id TEXT PRIMARY KEY,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    version_id TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('envio','rechazo','aprobacion','anulacion')),
    motivo TEXT NOT NULL DEFAULT '',
    usuario_id TEXT NOT NULL,
    usuario_nombre TEXT NOT NULL,
    fecha_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exportacion_fiscal (
    id TEXT PRIMARY KEY,
    lote_id TEXT NOT NULL REFERENCES lote(id),
    tipo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    usuario_id TEXT NOT NULL,
    usuario_nombre TEXT NOT NULL,
    fecha_utc TEXT NOT NULL,
    UNIQUE (lote_id, tipo)
);

CREATE TABLE IF NOT EXISTS notificacion (
    id TEXT PRIMARY KEY,
    destinatario_id TEXT,
    destinatario_rol TEXT,
    lote_id TEXT,
    tipo TEXT NOT NULL,
    texto TEXT NOT NULL,
    creado_utc TEXT NOT NULL,
    leida_utc TEXT
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_nombre TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    action_type TEXT NOT NULL,
    lote_id TEXT,
    version_id TEXT,
    detalle_json TEXT NOT NULL DEFAULT '{}',
    hash_previo TEXT NOT NULL,
    hash TEXT NOT NULL
);

-- Cuentas. No se borran (la auditoría las nombra): se desactivan.
CREATE TABLE IF NOT EXISTS usuario (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre TEXT NOT NULL,
    rol TEXT NOT NULL CHECK (rol IN ('empleado','supervisor')),
    principal INTEGER NOT NULL DEFAULT 0,
    gestiona_usuarios INTEGER NOT NULL DEFAULT 0,
    hash_clave TEXT,
    debe_cambiar_clave INTEGER NOT NULL DEFAULT 0,
    totp_cifrado TEXT,
    totp_ultimo_paso INTEGER,
    recuperacion_json TEXT NOT NULL DEFAULT '[]',
    activo INTEGER NOT NULL DEFAULT 1,
    intentos_fallidos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta TEXT,
    version_token INTEGER NOT NULL DEFAULT 1,
    creado_por TEXT,
    creado_utc TEXT NOT NULL,
    actualizado_utc TEXT NOT NULL,
    ultimo_ingreso_utc TEXT
);
-- Una fila por token de renovación; la familia es la sesión (se rota en cada uso).
CREATE TABLE IF NOT EXISTS sesion (
    hash_token TEXT PRIMARY KEY,
    familia TEXT NOT NULL,
    usuario_id TEXT NOT NULL REFERENCES usuario(id),
    creado_utc TEXT NOT NULL,
    expira_utc TEXT NOT NULL,
    vence_absoluto_utc TEXT NOT NULL,
    usado_utc TEXT,
    revocado_utc TEXT,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sesion_familia ON sesion(familia);
-- Pasos que faltan entre la contraseña y la sesión (cambiar la clave, 2FA).
CREATE TABLE IF NOT EXISTS desafio (
    hash_token TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL REFERENCES usuario(id),
    pasos_json TEXT NOT NULL,
    totp_pendiente TEXT,
    -- 0 = lo pidió alguien que ya está adentro (activar 2FA desde «Mi cuenta»).
    abre_sesion INTEGER NOT NULL DEFAULT 1,
    intentos INTEGER NOT NULL DEFAULT 0,
    expira_utc TEXT NOT NULL
);
-- Enlace de un solo uso para elegir la contraseña de la cuenta principal.
CREATE TABLE IF NOT EXISTS configuracion_inicial (
    hash_token TEXT PRIMARY KEY,
    expira_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS usuario_sin_delete BEFORE DELETE ON usuario
BEGIN SELECT RAISE(ABORT, 'Las cuentas no se borran: se desactivan'); END;

CREATE INDEX IF NOT EXISTS ix_partida_version ON partida(version_id);
CREATE INDEX IF NOT EXISTS ix_auditoria_lote ON auditoria(lote_id);
CREATE INDEX IF NOT EXISTS ix_adjunto_lote ON adjunto(lote_id);

-- Auditoría: solo se agrega.
CREATE TRIGGER IF NOT EXISTS auditoria_sin_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_sin_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'La auditoría no se puede borrar'); END;

-- Lote: nunca se borra; aprobado y anulado no cambian más.
CREATE TRIGGER IF NOT EXISTS lote_sin_delete BEFORE DELETE ON lote
BEGIN SELECT RAISE(ABORT, 'Los lotes no se borran: se anulan'); END;
CREATE TRIGGER IF NOT EXISTS lote_cerrado BEFORE UPDATE ON lote
WHEN OLD.estado IN ('aprobado','anulado')
BEGIN SELECT RAISE(ABORT, 'El lote está cerrado y no se puede modificar'); END;

-- Versión sellada (enviada a revisión): inmutable, igual que sus partidas.
CREATE TRIGGER IF NOT EXISTS version_sin_delete BEFORE DELETE ON lote_version
BEGIN SELECT RAISE(ABORT, 'Las versiones no se borran'); END;
CREATE TRIGGER IF NOT EXISTS version_sellada BEFORE UPDATE ON lote_version
WHEN OLD.sellada_utc IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'La versión ya fue enviada y no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS partida_sellada_upd BEFORE UPDATE ON partida
WHEN (SELECT sellada_utc FROM lote_version WHERE id = OLD.version_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'La versión ya fue enviada y no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS partida_sin_delete BEFORE DELETE ON partida
BEGIN SELECT RAISE(ABORT, 'Las partidas no se borran'); END;
"""

# Tablas hijas de un lote: con el lote aprobado no se agrega, cambia ni borra nada.
_HIJAS = ("lote_version", "partida", "adjunto", "observacion", "control_revision", "decision")
_TRIGGERS_APROBADO = "\n".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {tabla}_aprobado_{evento.lower()} BEFORE {evento} ON {tabla}
WHEN (SELECT estado FROM lote WHERE id = {fila}.lote_id) IN ('aprobado','anulado')
BEGIN SELECT RAISE(ABORT, 'El lote está cerrado y no se puede modificar'); END;"""
    for tabla in _HIJAS
    for evento, fila in (("INSERT", "NEW"), ("UPDATE", "OLD"), ("DELETE", "OLD"))
) + """
CREATE TRIGGER IF NOT EXISTS exportacion_solo_aprobado BEFORE INSERT ON exportacion_fiscal
WHEN (SELECT estado FROM lote WHERE id = NEW.lote_id) <> 'aprobado'
BEGIN SELECT RAISE(ABORT, 'Solo se exporta un lote aprobado'); END;
CREATE TRIGGER IF NOT EXISTS exportacion_sin_cambios BEFORE UPDATE ON exportacion_fiscal
BEGIN SELECT RAISE(ABORT, 'La exportación no se puede modificar'); END;
CREATE TRIGGER IF NOT EXISTS exportacion_sin_delete BEFORE DELETE ON exportacion_fiscal
BEGIN SELECT RAISE(ABORT, 'La exportación no se puede borrar'); END;
"""

# Columnas agregadas después de la primera versión de la base (tabla, columna, definición).
_COLUMNAS_NUEVAS = (
    ("lote_version", "correccion_checker", "INTEGER NOT NULL DEFAULT 0"),
    ("partida", "importe_ajustado", "TEXT"),
    ("partida", "excluida", "INTEGER NOT NULL DEFAULT 0"),
    ("partida", "editado_por", "TEXT NOT NULL DEFAULT ''"),
    ("partida", "editado_rol", "TEXT NOT NULL DEFAULT ''"),
    ("partida", "editado_utc", "TEXT"),
)

_iniciadas: set[Path] = set()
_lock = threading.Lock()


def ruta_db() -> Path:
    return Path(settings.DIRECTORIO_DATOS) / "retenciones.db"


def directorio_archivos() -> Path:
    carpeta = Path(settings.DIRECTORIO_DATOS) / "archivos"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _agregar_columnas(conexion: sqlite3.Connection) -> None:
    for tabla, columna, definicion in _COLUMNAS_NUEVAS:
        if columna not in {f["name"] for f in conexion.execute(f"PRAGMA table_info({tabla})")}:
            conexion.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def _conectar() -> sqlite3.Connection:
    ruta = ruta_db()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(ruta, timeout=15, isolation_level=None)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")
    with _lock:
        if ruta not in _iniciadas:
            conexion.execute("PRAGMA journal_mode = WAL")
            conexion.executescript(ESQUEMA + _TRIGGERS_APROBADO)
            _agregar_columnas(conexion)
            _iniciadas.add(ruta)
            from app.services import migracion

            migracion.migrar_revisiones_json(conexion)
            from app.services import cuentas

            cuentas.asegurar_principal(conexion)
    return conexion


@contextmanager
def transaccion(escritura: bool = True) -> Iterator[sqlite3.Connection]:
    """Una conexión en transacción: confirma al salir, deshace si hay excepción."""
    conexion = _conectar()
    try:
        conexion.execute("BEGIN IMMEDIATE" if escritura else "BEGIN")
        try:
            yield conexion
        except BaseException:
            conexion.execute("ROLLBACK")
            raise
        conexion.execute("COMMIT")
    finally:
        conexion.close()
