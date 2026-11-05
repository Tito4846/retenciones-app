"""
Lotes de conciliación con control Maker-Checker.

Ciclo: borrador -> pendiente -> en_revision -> rechazado | aprobado (y anulado
desde cualquiera que no esté cerrado). Ver docs/diseno-revision.md.

* El empleado (maker) crea el lote desde un cruce, justifica, adjunta
  respaldos, sube versiones y lo envía.
* Un supervisor (checker) que no lo haya preparado lo toma, tilda controles, observa y rechaza (con motivo)
  o aprueba. Nunca sobre un lote en el que haya cargado alguna versión. Mientras
  lo revisa puede corregirlo sin devolverlo: eso arma una versión aparte, que
  no lo vuelve autor del lote y se sella al aprobar o devolver.
* Aprobado es terminal: la base no deja cambiar nada (triggers) y se habilita
  la exportación fiscal.

Cada versión guarda su Excel. Mientras no se envía, la versión vigente es un
borrador que se puede corregir en la pantalla; al enviarla queda sellada, y
corregir algo después crea la versión siguiente.
"""

import csv
import hashlib
import io
import json
import mimetypes
import os
import sqlite3
import stat
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.core.config import settings
from app.core.db import directorio_archivos, transaccion
from app.core.usuarios import Usuario
from app.services import auditoria, excel_lote, exportador
from app.services import partidas as P
from app.services import validacion_excel as V
from app.services.auditoria import ahora_utc

ESTADOS = ("borrador", "pendiente", "en_revision", "rechazado", "aprobado", "anulado")
EDITABLES = ("borrador", "rechazado")
ORIGENES_OBSERVACION = ("nuestro", "cliente", "arca")

CONTROLES = {
    "faltan_en_erp": "Faltan en el Libro Mayor: verificadas una por una",
    "faltan_en_fisco": "Faltan en ARCA: revisada la procedencia y el agente",
    "diferencias": "Diferencias y comprobantes: las justificaciones son razonables",
    "respaldos": "Respaldos abiertos y suficientes",
    "datos": "Cliente, CUIT, período y totales correctos",
}

EXPORTACIONES = {
    "libro_csv": ("Libro de retenciones (CSV)", "csv"),
    "ddjj_txt": ("Retenciones computables para la DDJJ (TXT)", "txt"),
}

ADJUNTOS_PERMITIDOS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".xls", ".csv", ".txt"}
MOTIVO_MINIMO = 15
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ErrorDeLote(Exception):
    """Error para la pantalla. `estado` es el código HTTP."""

    def __init__(self, estado: int, codigo: str, mensaje: str, errores: list[dict] | None = None, **extra):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje, self.errores, self.extra = estado, codigo, mensaje, errores or [], extra

    def detalle(self) -> dict:
        return {"codigo": self.codigo, "mensaje": self.mensaje, "errores": self.errores, **self.extra}


def _prohibido(mensaje: str, codigo: str = "SIN_PERMISO") -> ErrorDeLote:
    return ErrorDeLote(403, codigo, mensaje)


def _conflicto(mensaje: str, codigo: str = "ESTADO", errores: list[dict] | None = None) -> ErrorDeLote:
    return ErrorDeLote(409, codigo, mensaje, errores)


# ----------------------------------------------------------------------
# Archivos (guardados por hash: el mismo contenido es el mismo archivo)
# ----------------------------------------------------------------------


def _sha(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def ruta_archivo(sha: str, extension: str) -> Path:
    return directorio_archivos() / f"{sha}{extension}"


def _guardar_archivo(contenido: bytes, extension: str) -> str:
    sha = _sha(contenido)
    ruta = ruta_archivo(sha, extension)
    if not ruta.exists():
        temporal = ruta.with_suffix(ruta.suffix + ".tmp")
        temporal.write_bytes(contenido)
        temporal.replace(ruta)
        os.chmod(ruta, stat.S_IREAD)
    return sha


def _en_uso(c: sqlite3.Connection, sha: str) -> bool:
    return c.execute(
        "SELECT 1 FROM lote_version WHERE sha256 = ? UNION ALL SELECT 1 FROM adjunto WHERE sha256 = ? "
        "UNION ALL SELECT 1 FROM exportacion_fiscal WHERE sha256 = ? LIMIT 1", (sha, sha, sha)).fetchone() is not None


def soltar_archivos(*shas: str) -> int:
    """
    Borra del disco los archivos que ya nadie usa. Solo pasa con el Excel de un
    borrador, que se regenera en cada justificación: lo enviado, los adjuntos y
    las exportaciones siguen referenciados y no se tocan. Sin argumentos, barre
    toda la carpeta. Se llama después de confirmar la transacción, nunca dentro.
    """
    carpeta = directorio_archivos()
    candidatos = [carpeta / f"{s}.xlsx" for s in shas if s] if shas else list(carpeta.iterdir())
    borrados = 0
    with transaccion(escritura=False) as c:
        for ruta in candidatos:
            sha = ruta.name.split(".")[0]
            # .tmp: un archivo que otro pedido está escribiendo en este momento.
            if ruta.is_file() and ruta.suffix != ".tmp" and len(sha) == 64 and not _en_uso(c, sha):
                os.chmod(ruta, stat.S_IREAD | stat.S_IWRITE)
                ruta.unlink()
                borrados += 1
    return borrados


def _leer_version(version: dict) -> bytes:
    return ruta_archivo(version["sha256"], ".xlsx").read_bytes()


# ----------------------------------------------------------------------
# Lectura
# ----------------------------------------------------------------------


def _lote(c: sqlite3.Connection, lote_id: str) -> dict:
    fila = c.execute("SELECT * FROM lote WHERE id = ?", (lote_id,)).fetchone()
    if fila is None:
        raise ErrorDeLote(404, "NO_EXISTE", "El lote no existe.")
    return dict(fila)


def _version(c: sqlite3.Connection, lote_id: str, numero: int) -> dict:
    fila = c.execute("SELECT * FROM lote_version WHERE lote_id = ? AND numero = ?", (lote_id, numero)).fetchone()
    if fila is None:
        raise ErrorDeLote(404, "NO_EXISTE", f"El lote no tiene versión v{numero}.")
    return dict(fila)


def _partidas(c: sqlite3.Connection, version_id: str) -> list[dict]:
    filas = c.execute("SELECT * FROM partida WHERE version_id = ? ORDER BY orden", (version_id,))
    return [P.a_json(dict(f)) for f in filas]


def _adjuntos(c: sqlite3.Connection, lote_id: str, activos: bool = True) -> list[dict]:
    sql = "SELECT * FROM adjunto WHERE lote_id = ?" + (" AND retirado_utc IS NULL" if activos else "") + " ORDER BY creado_utc"
    return [dict(f) for f in c.execute(sql, (lote_id,))]


def _autores(c: sqlite3.Connection, lote: dict) -> set[str]:
    # La corrección del supervisor durante la revisión no lo hace autor: si no, no podría aprobar.
    return {lote["maker_id"]} | {f["autor_id"] for f in c.execute(
        "SELECT autor_id FROM lote_version WHERE lote_id = ? AND correccion_checker = 0", (lote["id"],))}


def _version_vigente(c: sqlite3.Connection, lote: dict) -> dict:
    return _version(c, lote["id"], lote["version_vigente"])


# ----------------------------------------------------------------------
# Permisos y compuertas
# ----------------------------------------------------------------------


def _exigir_rol(usuario: Usuario, *roles: str) -> None:
    if not any(usuario.tiene(r) for r in roles):
        raise _prohibido(f"Necesitás el rol {' o '.join(roles)} para esto.")


def _exigir_estado(lote: dict, *estados: str) -> None:
    if lote["estado"] not in estados:
        if lote["estado"] == "aprobado":
            raise _conflicto("El lote está aprobado: es definitivo y no se puede modificar.", "CERRADO")
        if lote["estado"] == "anulado":
            raise _conflicto("El lote está anulado.", "CERRADO")
        raise _conflicto(f"No se puede hacer con el lote «{lote['estado'].replace('_', ' ')}».")


def _exigir_version(lote: dict, version: int | None) -> None:
    if version is not None and version != lote["version_vigente"]:
        raise _conflicto(f"Estás viendo v{version} y la vigente es v{lote['version_vigente']}: actualizá la pantalla.", "VERSION_VIEJA")


def _exigir_checker(lote: dict, usuario: Usuario) -> None:
    _exigir_rol(usuario, "supervisor")
    if lote["checker_id"] != usuario.id:
        raise _prohibido("Este lote lo está revisando otra persona.", "NO_ASIGNADO")


def _exigir_maker(usuario: Usuario) -> None:
    # El supervisor también puede preparar (no aprobar lo suyo: segregación de funciones).
    _exigir_rol(usuario, "empleado", "supervisor")


def _exigir_edicion(lote: dict, usuario: Usuario) -> None:
    """Quién corrige ahora: el que prepara mientras está abierto, o el supervisor que lo revisa."""
    if lote["estado"] == "en_revision":
        _exigir_checker(lote, usuario)
    else:
        _exigir_estado(lote, *EDITABLES)


def pesos(importe: str | None) -> str:
    """'15000.5' -> '$ 15.000,50' (como se lee en Argentina)."""
    numero = Decimal(importe or "0")
    return "$ " + f"{numero:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _faltas_partidas(c: sqlite3.Connection, lote: dict, partidas: list[dict]) -> list[dict]:
    con_adjunto = {a["clave_partida"] for a in _adjuntos(c, lote["id"]) if a["clave_partida"]}
    errores = []
    for p in partidas:
        for codigo, mensaje in V.requisitos(p, p["clave"] in con_adjunto):
            errores.append({"codigo": codigo, "mensaje": f"{mensaje}: {p['razon_social'] or p['cuit'] or p['documento_erp']} "
                            f"({p['estado']}, {pesos(p['importe_fisco'] or p['importe_erp'])}).", "clave": p["clave"]})
    return errores


def compuertas(c: sqlite3.Connection, lote: dict, para: str) -> list[dict]:
    """Lo que falta para enviar o aprobar (vacío = se puede)."""
    if lote["version_vigente"] == 0:
        return [{"codigo": "SIN_VERSION", "mensaje": "El lote no tiene versiones."}]
    version = _version_vigente(c, lote)
    partidas = _partidas(c, version["id"])
    errores = []
    validacion = json.loads(version["validacion_json"] or "{}")
    if validacion and not validacion.get("valida", True):
        errores.append({"codigo": "VERSION_INVALIDA", "mensaje": f"La versión v{version['numero']} no pasó la validación."})
    if not partidas:
        errores.append({"codigo": "SIN_PARTIDAS", "mensaje": "La versión no tiene partidas: regenerala desde una conciliación."})
    errores += _faltas_partidas(c, lote, partidas)
    errores += V.cuadre(partidas, version["total_libro_erp"], version["total_fisco"])
    neta = P.totales_netos(partidas)["neta"]
    if Decimal(neta) != 0:
        errores.append({"codigo": "DIFERENCIA_NETA", "mensaje": f"La diferencia neta es {pesos(neta)}: tiene que quedar en $ 0,00."})
    if para == "aprobar":
        marcados = {f["control"]: f["ok"] for f in c.execute(
            "SELECT control, ok FROM control_revision WHERE lote_id = ? AND version_id = ?", (lote["id"], version["id"]))}
        faltan = [texto for clave, texto in CONTROLES.items() if not marcados.get(clave)]
        if faltan:
            errores.append({"codigo": "CONTROLES", "mensaje": "Faltan tildar controles: " + "; ".join(faltan) + "."})
        abiertas = c.execute("SELECT COUNT(*) FROM observacion WHERE lote_id = ? AND resuelta_utc IS NULL", (lote["id"],)).fetchone()[0]
        if abiertas:
            errores.append({"codigo": "OBSERVACIONES", "mensaje": f"Hay {abiertas} observación(es) sin resolver."})
    return errores


# ----------------------------------------------------------------------
# Versiones
# ----------------------------------------------------------------------


def _nombre_version(lote: dict, numero: int) -> str:
    return exportador.nombre_archivo(lote["empresa"]).replace(".xlsx", f" v{numero}.xlsx")


def _meta(lote: dict, numero: int) -> dict:
    return {"lote_id": lote["id"], "version": numero, "generado_utc": ahora_utc(), "empresa": lote["empresa"], "periodo": lote["periodo"]}


def _insertar_partidas(c: sqlite3.Connection, lote_id: str, version_id: str, partidas: list[dict]) -> None:
    campos = ("orden", "clave", "estado", "codigo", "flags", "criterio", "cuit", "razon_social", "impuesto", "certificado",
              "documento_erp", "fecha_fisco", "fecha_erp", "importe_fisco", "importe_erp", "diferencia",
              "justificacion", "comentario", "hash_fila", *P.CAMPOS_EDICION)
    c.executemany(
        f"INSERT INTO partida (lote_id, version_id, {', '.join(campos)}) VALUES (?, ?, {', '.join('?' * len(campos))})",
        [(lote_id, version_id, *(json.dumps(p["flags"]) if k == "flags" else p.get(k) for k in campos)) for p in partidas],
    )


def _insertar_version(c: sqlite3.Connection, lote: dict, numero: int, origen: str, contenido: bytes, usuario: Usuario,
                      partidas: list[dict], totales: tuple[str, str], validacion: dict, nombre: str | None = None,
                      correccion_checker: bool = False) -> dict:
    sha = _guardar_archivo(contenido, ".xlsx")
    version = {
        "id": uuid.uuid4().hex, "lote_id": lote["id"], "numero": numero, "origen": origen, "sha256": sha,
        "nombre_archivo": nombre or _nombre_version(lote, numero), "autor_id": usuario.id, "autor_nombre": usuario.nombre,
        "total_libro_erp": totales[0], "total_fisco": totales[1], "resumen_json": json.dumps(P.resumen(partidas)),
        "validacion_json": json.dumps(validacion), "creado_utc": ahora_utc(), "correccion_checker": int(correccion_checker),
    }
    c.execute(f"INSERT INTO lote_version ({', '.join(version)}) VALUES ({', '.join('?' * len(version))})", list(version.values()))
    _insertar_partidas(c, lote["id"], version["id"], partidas)
    if correccion_checker:  # sigue en revisión, con el mismo supervisor
        c.execute("UPDATE lote SET version_vigente = ?, actualizado_utc = ? WHERE id = ?", (numero, ahora_utc(), lote["id"]))
    else:
        c.execute("UPDATE lote SET version_vigente = ?, estado = 'borrador', checker_id = NULL, checker_nombre = NULL, "
                  "actualizado_utc = ? WHERE id = ?", (numero, ahora_utc(), lote["id"]))
    return version


def _sellar(c: sqlite3.Connection, version: dict) -> None:
    if not version["sellada_utc"]:
        c.execute("UPDATE lote_version SET sellada_utc = ? WHERE id = ?", (ahora_utc(), version["id"]))


def _validacion_generada(partidas: list[dict], totales: tuple[str, str], avisos: list[dict] | None = None) -> dict:
    errores = [{"etapa": 5, **e} for e in V.cuadre(partidas, *totales)]
    return {"valida": not errores, "etapa_fallida": 5 if errores else None, "errores": errores,
            "advertencias": avisos or [], "resumen": P.resumen(partidas)}


def _desde_cruce(tarea) -> tuple[list[dict], tuple[str, str], bytes]:
    if tarea is None or tarea.status != "completado" or tarea.entrada is None:
        raise ErrorDeLote(404, "SIN_CONCILIACION", "No hay una conciliación terminada: volvé a traer los datos.")
    partidas = P.armar(tarea.resultado, tarea.entrada)
    totales = P.totales_fuente(tarea.resultado, tarea.entrada)
    return partidas, totales, exportador.exportar(tarea.resultado, tarea.entrada)


def crear(usuario: Usuario, tarea, empresa: str, cuit_empresa: str, periodo: str, comentario: str = "",
          rectifica_a: str | None = None) -> dict:
    _exigir_maker(usuario)
    partidas, totales, base = _desde_cruce(tarea)
    empresa = empresa.strip() or "Sin nombre"
    with transaccion() as c:
        if rectifica_a:
            original = _lote(c, rectifica_a)
            if original["estado"] != "aprobado":
                raise _conflicto("Solo se rectifica un lote aprobado.")
            empresa, cuit_empresa, periodo = original["empresa"], original["cuit_empresa"], original["periodo"]
        else:
            repetido = c.execute(
                "SELECT id FROM lote WHERE estado <> 'anulado' AND periodo = ? AND "
                "((? <> '' AND cuit_empresa = ?) OR (? = '' AND empresa = ?)) AND id NOT IN "
                "(SELECT rectifica_a FROM lote WHERE rectifica_a IS NOT NULL)",
                (periodo.strip(), cuit_empresa.strip(), cuit_empresa.strip(), cuit_empresa.strip(), empresa)).fetchone()
            if repetido:
                raise _conflicto(f"Ya hay un lote de {empresa} para {periodo}: subí una versión nueva a ese, o anulalo.",
                                 "LOTE_REPETIDO")
        ahora = ahora_utc()
        lote = {
            "id": uuid.uuid4().hex[:12], "empresa": empresa, "cuit_empresa": cuit_empresa.strip(), "periodo": periodo.strip(),
            "estado": "borrador", "version_vigente": 0, "maker_id": usuario.id, "maker_nombre": usuario.nombre,
            "total_libro_erp": totales[0], "total_fisco": totales[1], "rectifica_a": rectifica_a,
            "creado_utc": ahora, "actualizado_utc": ahora,
        }
        c.execute(f"INSERT INTO lote ({', '.join(lote)}) VALUES ({', '.join('?' * len(lote))})", list(lote.values()))
        contenido = excel_lote.generar(base, partidas, _meta(lote, 1))
        version = _insertar_version(c, lote, 1, "generado", contenido, usuario, partidas, totales,
                                    _validacion_generada(partidas, totales))
        auditoria.registrar(c, usuario, "lote_rectificativo" if rectifica_a else "lote_creado", lote["id"], None,
                            empresa=empresa, periodo=lote["periodo"], comentario=comentario.strip(), rectifica_a=rectifica_a)
        auditoria.registrar(c, usuario, "version_generada", lote["id"], version["id"], numero=1, sha256=version["sha256"],
                            total_libro_erp=totales[0], total_fisco=totales[1])
        return _detalle(c, lote["id"])


def regenerar(usuario: Usuario, lote_id: str, tarea, version_base: int) -> dict:
    """Versión nueva desde una conciliación rehecha. Las justificaciones se copian por partida."""
    _exigir_maker(usuario)
    partidas, totales, base = _desde_cruce(tarea)
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, *EDITABLES)
        _exigir_version(lote, version_base)
        anterior = _version_vigente(c, lote)
        previas = {p["clave"]: p for p in _partidas(c, anterior["id"])}
        for p in partidas:
            if p["clave"] in previas:
                for campo in (*P.CAMPOS_EDITABLES, *P.CAMPOS_EDICION):
                    p[campo] = previas[p["clave"]][campo]
        avisos = []
        if totales != (anterior["total_libro_erp"], anterior["total_fisco"]):
            avisos.append({"etapa": 5, "codigo": "FUENTE_CAMBIO", "mensaje": (
                f"Los totales de las fuentes cambiaron respecto de v{anterior['numero']}: "
                f"ERP {anterior['total_libro_erp']} → {totales[0]}, Fisco {anterior['total_fisco']} → {totales[1]}.")})
        numero = anterior["numero"] + 1
        contenido = excel_lote.generar(base, partidas, _meta(lote, numero))
        version = _insertar_version(c, lote, numero, "generado", contenido, usuario, partidas, totales,
                                    _validacion_generada(partidas, totales, avisos))
        auditoria.registrar(c, usuario, "version_generada", lote_id, version["id"], numero=numero, sha256=version["sha256"],
                            total_libro_erp=totales[0], total_fisco=totales[1])
        return _detalle(c, lote_id)


def subir_version(usuario: Usuario, lote_id: str, contenido: bytes, nombre: str, version_base: int) -> dict:
    """El Excel trabajado afuera vuelve como versión nueva, si pasa el pipeline de validación."""
    _exigir_maker(usuario)
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, *EDITABLES)
        _exigir_version(lote, version_base)
        base = _version_vigente(c, lote)
        resultado, nuevas = V.validar(contenido, lote, base, _partidas(c, base["id"]))
        if resultado.errores:
            auditoria.registrar(c, usuario, "carga_rechazada", lote_id, base["id"], archivo=nombre, sha256=_sha(contenido),
                                etapa=resultado.etapa_fallida, errores=resultado.errores[:50])
            rechazo = ErrorDeLote(422, "VALIDACION", f"El archivo no pasó la validación (etapa {resultado.etapa_fallida}).",
                                  resultado.errores, validacion=resultado.a_dict())
        else:
            numero = base["numero"] + 1
            version = _insertar_version(c, lote, numero, "subido", contenido, usuario, nuevas,
                                        (base["total_libro_erp"], base["total_fisco"]), resultado.a_dict(), nombre)
            auditoria.registrar(c, usuario, "version_subida", lote_id, version["id"], numero=numero, archivo=nombre,
                                sha256=version["sha256"], cambiadas=resultado.resumen.get("cambiadas"))
            return _detalle(c, lote_id)
    raise rechazo


def _importe_ajustado(valor: str | None) -> str | None:
    if valor is None or not str(valor).strip():
        return None
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        numero = None
    if numero is None or not numero.is_finite() or numero < 0 or numero >= Decimal("1e15"):
        raise ErrorDeLote(422, "IMPORTE_INVALIDO", f"«{valor}» no es un importe válido.")
    return str(numero.quantize(Decimal("0.01")))


def actualizar_partida(usuario: Usuario, lote_id: str, clave: str, version: int, cambios: dict) -> dict:
    """
    Corrección en pantalla: justificación, comentario, importe ajustado o sacar
    la partida del cruce (`cambios` trae solo lo que se tocó). Las fuentes no se
    tocan. Sobre una versión sellada el cambio crea la versión siguiente: si la
    corrige el supervisor que revisa, el lote sigue en revisión, sin devolverse.
    """
    _exigir_maker(usuario)
    justificacion, comentario = cambios.get("justificacion"), cambios.get("comentario")
    if justificacion and justificacion not in P.JUSTIFICACIONES:
        raise ErrorDeLote(422, "JUSTIFICACION_INVALIDA", "Justificación desconocida.")
    if comentario is not None and len(comentario) > 2000:
        raise ErrorDeLote(422, "COMENTARIO_LARGO", "El comentario supera los 2000 caracteres.")
    nuevos = {k: v for k, v in cambios.items() if v is not None}
    if "comentario" in nuevos:
        nuevos["comentario"] = nuevos["comentario"].strip()
    if "excluida" in nuevos:
        nuevos["excluida"] = int(nuevos["excluida"])
    if "importe_ajustado" in cambios:  # null borra el ajuste
        nuevos["importe_ajustado"] = _importe_ajustado(cambios["importe_ajustado"])
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_edicion(lote, usuario)
        _exigir_version(lote, version)
        revisando = lote["estado"] == "en_revision"
        vigente = _version_vigente(c, lote)
        partidas = _partidas(c, vigente["id"])
        partida = next((p for p in partidas if p["clave"] == clave), None)
        if partida is None:
            raise ErrorDeLote(404, "NO_EXISTE", "La partida no existe en la versión vigente.")
        antes = {k: partida[k] for k in ("justificacion", "comentario", "importe_ajustado", "excluida")}
        despues = {**antes, **nuevos}
        if antes == despues:
            return _detalle(c, lote_id)
        if partida["estado"] == P.CONCILIADO and (despues["importe_ajustado"], despues["excluida"]) != (antes["importe_ajustado"], antes["excluida"]):
            raise ErrorDeLote(422, "CONCILIADA", "Una partida que concilió sola no se ajusta ni se quita.")
        if despues["excluida"] and not despues["comentario"]:
            raise ErrorDeLote(422, "MOTIVO", "Para quitar una partida contá el motivo.")
        partida.update(despues, editado_por=usuario.nombre, editado_utc=ahora_utc(),
                       editado_rol="supervisor" if usuario.tiene("supervisor") else "empleado")
        totales = (vigente["total_libro_erp"], vigente["total_fisco"])
        if vigente["sellada_utc"]:
            numero = vigente["numero"] + 1
            contenido = excel_lote.generar(_leer_version(vigente), partidas, _meta(lote, numero))
            nueva = _insertar_version(c, lote, numero, "edicion", contenido, usuario, partidas, totales,
                                      _validacion_generada(partidas, totales), correccion_checker=revisando)
            if revisando:  # lo que ya tildó sigue tildado: es la misma revisión
                c.execute("INSERT INTO control_revision SELECT lote_id, ?, control, ok, usuario_id, usuario_nombre, fecha_utc "
                          "FROM control_revision WHERE lote_id = ? AND version_id = ?", (nueva["id"], lote_id, vigente["id"]))
            auditoria.registrar(c, usuario, "version_generada", lote_id, nueva["id"], numero=numero, sha256=nueva["sha256"],
                                motivo="corrección del supervisor en revisión" if revisando else "corrección después de enviar")
            version_id = nueva["id"]
        else:
            c.execute("UPDATE partida SET justificacion = ?, comentario = ?, importe_ajustado = ?, excluida = ?, editado_por = ?, "
                      "editado_rol = ?, editado_utc = ? WHERE version_id = ? AND clave = ?",
                      (partida["justificacion"], partida["comentario"], partida["importe_ajustado"], partida["excluida"],
                       partida["editado_por"], partida["editado_rol"], partida["editado_utc"], vigente["id"], clave))
            contenido = excel_lote.generar(_leer_version(vigente), partidas, _meta(lote, vigente["numero"]))
            c.execute("UPDATE lote_version SET sha256 = ?, resumen_json = ? WHERE id = ?",
                      (_guardar_archivo(contenido, ".xlsx"), json.dumps(P.resumen(partidas)), vigente["id"]))
            version_id = vigente["id"]
            reemplazado = vigente["sha256"]
        auditoria.registrar(c, usuario, "partida_actualizada", lote_id, version_id, clave=clave, antes=antes, despues=despues)
        detalle = _detalle(c, lote_id)
    if not vigente["sellada_utc"]:
        soltar_archivos(reemplazado)
    return detalle


# ----------------------------------------------------------------------
# Adjuntos
# ----------------------------------------------------------------------


def subir_adjunto(usuario: Usuario, lote_id: str, contenido: bytes, nombre: str, clave_partida: str | None) -> dict:
    _exigir_maker(usuario)
    nombre = Path(nombre or "respaldo").name[:150]
    extension = Path(nombre).suffix.lower()
    if extension not in ADJUNTOS_PERMITIDOS:
        raise ErrorDeLote(422, "TIPO_NO_PERMITIDO", f"Tipo de archivo no permitido ({extension or 'sin extensión'}). "
                          f"Se aceptan: {', '.join(sorted(ADJUNTOS_PERMITIDOS))}.")
    if not contenido:
        raise ErrorDeLote(422, "VACIO", "El archivo está vacío.")
    if len(contenido) > settings.MAX_MB_ARCHIVO * 1024 * 1024:
        raise ErrorDeLote(413, "ARCHIVO_GRANDE", f"El archivo supera los {settings.MAX_MB_ARCHIVO} MB.")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_edicion(lote, usuario)
        vigente = _version_vigente(c, lote)
        if clave_partida and not c.execute("SELECT 1 FROM partida WHERE version_id = ? AND clave = ?",
                                           (vigente["id"], clave_partida)).fetchone():
            raise ErrorDeLote(404, "NO_EXISTE", "La partida no existe en la versión vigente.")
        adjunto = {
            "id": uuid.uuid4().hex[:12], "lote_id": lote_id, "clave_partida": clave_partida or None, "version_id": vigente["id"],
            "nombre": nombre, "mime": mimetypes.guess_type(nombre)[0] or "application/octet-stream", "tamano": len(contenido),
            "sha256": _guardar_archivo(contenido, extension), "autor_id": usuario.id, "autor_nombre": usuario.nombre,
            "creado_utc": ahora_utc(),
        }
        c.execute(f"INSERT INTO adjunto ({', '.join(adjunto)}) VALUES ({', '.join('?' * len(adjunto))})", list(adjunto.values()))
        auditoria.registrar(c, usuario, "adjunto_subido", lote_id, vigente["id"], adjunto=adjunto["id"], nombre=nombre,
                            sha256=adjunto["sha256"], clave_partida=clave_partida)
        return _detalle(c, lote_id)


def retirar_adjunto(usuario: Usuario, lote_id: str, adjunto_id: str) -> dict:
    """Los adjuntos no se borran: quedan retirados (y en la auditoría)."""
    _exigir_maker(usuario)
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_edicion(lote, usuario)
        cambio = c.execute("UPDATE adjunto SET retirado_utc = ?, retirado_por = ? WHERE id = ? AND lote_id = ? AND retirado_utc IS NULL",
                           (ahora_utc(), usuario.id, adjunto_id, lote_id))
        if not cambio.rowcount:
            raise ErrorDeLote(404, "NO_EXISTE", "El adjunto no existe.")
        auditoria.registrar(c, usuario, "adjunto_retirado", lote_id, None, adjunto=adjunto_id)
        return _detalle(c, lote_id)


def archivo_adjunto(usuario: Usuario, lote_id: str, adjunto_id: str) -> tuple[Path, str, str]:
    with transaccion() as c:
        fila = c.execute("SELECT * FROM adjunto WHERE id = ? AND lote_id = ?", (adjunto_id, lote_id)).fetchone()
        if fila is None:
            raise ErrorDeLote(404, "NO_EXISTE", "El adjunto no existe.")
        auditoria.registrar(c, usuario, "descarga", lote_id, None, adjunto=adjunto_id, nombre=fila["nombre"])
        return ruta_archivo(fila["sha256"], Path(fila["nombre"]).suffix.lower()), fila["nombre"], fila["mime"]


def archivo_version(usuario: Usuario, lote_id: str, numero: int) -> tuple[Path, str]:
    with transaccion() as c:
        version = _version(c, lote_id, numero)
        auditoria.registrar(c, usuario, "descarga", lote_id, version["id"], numero=numero, sha256=version["sha256"])
        return ruta_archivo(version["sha256"], ".xlsx"), version["nombre_archivo"]


# ----------------------------------------------------------------------
# Transiciones
# ----------------------------------------------------------------------


def _notificar(c: sqlite3.Connection, lote: dict, tipo: str, texto: str, destinatario_id: str | None = None,
               destinatario_rol: str | None = None) -> None:
    c.execute("INSERT INTO notificacion (id, destinatario_id, destinatario_rol, lote_id, tipo, texto, creado_utc) "
              "VALUES (?, ?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex[:12], destinatario_id, destinatario_rol, lote["id"], tipo, texto, ahora_utc()))


def _decision(c: sqlite3.Connection, lote: dict, version_id: str | None, tipo: str, motivo: str, usuario: Usuario) -> None:
    c.execute("INSERT INTO decision (id, lote_id, version_id, tipo, motivo, usuario_id, usuario_nombre, fecha_utc) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex[:12], lote["id"], version_id, tipo, motivo, usuario.id, usuario.nombre, ahora_utc()))


def enviar(usuario: Usuario, lote_id: str, version: int, comentario: str = "") -> dict:
    _exigir_maker(usuario)
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, *EDITABLES)
        _exigir_version(lote, version)
        faltas = compuertas(c, lote, "enviar")
        if faltas:
            raise _conflicto("Todavía no se puede enviar a revisión.", "COMPUERTA", faltas)
        vigente = _version_vigente(c, lote)
        _sellar(c, vigente)
        _decision(c, lote, vigente["id"], "envio", comentario.strip(), usuario)
        c.execute("UPDATE lote SET estado = 'pendiente', checker_id = NULL, checker_nombre = NULL, actualizado_utc = ? WHERE id = ?",
                  (ahora_utc(), lote_id))
        _notificar(c, lote, "para_revisar", f"{usuario.nombre} envió {lote['empresa']} {lote['periodo']} (v{vigente['numero']}) a revisión.",
                   destinatario_rol="supervisor")
        auditoria.registrar(c, usuario, "enviado", lote_id, vigente["id"], numero=vigente["numero"], comentario=comentario.strip())
        return _detalle(c, lote_id)


def tomar(usuario: Usuario, lote_id: str) -> dict:
    _exigir_rol(usuario, "supervisor")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        # Un supervisor puede quedarse con un lote que tomó otro (que se fue, o un usuario
        # que ya no existe, como los de la bandeja migrada). Queda en la auditoría.
        reasigna = lote["estado"] == "en_revision" and lote["checker_id"] != usuario.id
        if not reasigna:
            _exigir_estado(lote, "pendiente")
        if usuario.id in _autores(c, lote):
            auditoria.registrar(c, usuario, "acceso_denegado", lote_id, None, intento="tomar", motivo="segregacion_de_funciones")
            denegado = _prohibido("No podés revisar un lote que cargaste vos (segregación de funciones).", "SOD")
        else:
            c.execute("UPDATE lote SET estado = 'en_revision', checker_id = ?, checker_nombre = ?, actualizado_utc = ? WHERE id = ?",
                      (usuario.id, usuario.nombre, ahora_utc(), lote_id))
            if reasigna:
                auditoria.registrar(c, usuario, "reasignado", lote_id, None, checker_anterior=lote["checker_id"])
            else:
                auditoria.registrar(c, usuario, "tomado", lote_id, None)
            return _detalle(c, lote_id)
    raise denegado


def marcar_control(usuario: Usuario, lote_id: str, control: str, version: int, ok: bool) -> dict:
    if control not in CONTROLES:
        raise ErrorDeLote(404, "NO_EXISTE", "Control desconocido.")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "en_revision")
        _exigir_checker(lote, usuario)
        _exigir_version(lote, version)
        vigente = _version_vigente(c, lote)
        c.execute("INSERT OR REPLACE INTO control_revision VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (lote_id, vigente["id"], control, int(ok), usuario.id, usuario.nombre, ahora_utc()))
        auditoria.registrar(c, usuario, "control", lote_id, vigente["id"], control=control, ok=ok)
        return _detalle(c, lote_id)


def _insertar_observacion(c: sqlite3.Connection, lote: dict, version_id: str, texto: str, origen: str,
                          clave: str | None, usuario: Usuario) -> str:
    if not texto.strip():
        raise ErrorDeLote(422, "VACIA", "Escribí la observación.")
    if origen not in ORIGENES_OBSERVACION:
        raise ErrorDeLote(422, "ORIGEN", "Origen desconocido.")
    if clave and not c.execute("SELECT 1 FROM partida WHERE version_id = ? AND clave = ?", (version_id, clave)).fetchone():
        raise ErrorDeLote(404, "NO_EXISTE", "La partida no existe en la versión vigente.")
    obs_id = uuid.uuid4().hex[:10]
    c.execute("INSERT INTO observacion (id, lote_id, version_id, clave_partida, texto, origen, autor_id, autor_nombre, creado_utc) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (obs_id, lote["id"], version_id, clave or None, texto.strip(), origen,
                                                     usuario.id, usuario.nombre, ahora_utc()))
    return obs_id


def observar(usuario: Usuario, lote_id: str, version: int, texto: str, origen: str, clave: str | None = None) -> dict:
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "en_revision")
        _exigir_checker(lote, usuario)
        _exigir_version(lote, version)
        vigente = _version_vigente(c, lote)
        obs_id = _insertar_observacion(c, lote, vigente["id"], texto, origen, clave, usuario)
        auditoria.registrar(c, usuario, "observacion", lote_id, vigente["id"], observacion=obs_id, texto=texto.strip(),
                            origen=origen, clave_partida=clave)
        return _detalle(c, lote_id)


def resolver_observacion(usuario: Usuario, lote_id: str, observacion_id: str, resuelta: bool) -> dict:
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "en_revision")
        _exigir_checker(lote, usuario)
        valores = (ahora_utc(), usuario.id) if resuelta else (None, None)
        cambio = c.execute("UPDATE observacion SET resuelta_utc = ?, resuelta_por = ? WHERE id = ? AND lote_id = ?",
                           (*valores, observacion_id, lote_id))
        if not cambio.rowcount:
            raise ErrorDeLote(404, "NO_EXISTE", "La observación no existe.")
        auditoria.registrar(c, usuario, "observacion_resuelta" if resuelta else "observacion_reabierta", lote_id, None,
                            observacion=observacion_id)
        return _detalle(c, lote_id)


def rechazar(usuario: Usuario, lote_id: str, version: int, motivo: str, observaciones: list[dict]) -> dict:
    motivo = motivo.strip()
    if len(motivo) < MOTIVO_MINIMO:
        raise ErrorDeLote(422, "MOTIVO", f"Contá el motivo del rechazo (al menos {MOTIVO_MINIMO} caracteres), "
                          "por ejemplo: «Diferencia no conciliada de $15.000 en certificado N° 4512».")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "en_revision")
        _exigir_checker(lote, usuario)
        _exigir_version(lote, version)
        vigente = _version_vigente(c, lote)
        for o in observaciones:
            _insertar_observacion(c, lote, vigente["id"], o.get("texto", ""), o.get("origen", "nuestro"), o.get("clave"), usuario)
        _sellar(c, vigente)  # lo que corrigió el supervisor queda fijo: lo próximo es otra versión
        _decision(c, lote, vigente["id"], "rechazo", motivo, usuario)
        c.execute("UPDATE lote SET estado = 'rechazado', actualizado_utc = ? WHERE id = ?", (ahora_utc(), lote_id))
        _notificar(c, lote, "rechazado", f"{usuario.nombre} rechazó {lote['empresa']} {lote['periodo']}: {motivo}",
                   destinatario_id=lote["maker_id"])
        auditoria.registrar(c, usuario, "rechazado", lote_id, vigente["id"], motivo=motivo, observaciones=len(observaciones))
        return _detalle(c, lote_id)


def _hash_aprobado(c: sqlite3.Connection, lote: dict, version: dict) -> str:
    partes = [version["sha256"], *(p["hash_fila"] + p["justificacion"] + p["comentario"] + (p["importe_ajustado"] or "") + str(p["excluida"])
                                   for p in _partidas(c, version["id"])),
              *sorted(a["sha256"] for a in _adjuntos(c, lote["id"]))]
    return hashlib.sha256("\n".join(partes).encode("utf-8")).hexdigest()


def aprobar(usuario: Usuario, lote_id: str, version: int) -> dict:
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "en_revision")
        _exigir_checker(lote, usuario)
        if usuario.id in _autores(c, lote):
            raise _prohibido("No podés aprobar un lote que cargaste vos (segregación de funciones).", "SOD")
        _exigir_version(lote, version)
        faltas = compuertas(c, lote, "aprobar")
        if faltas:
            raise _conflicto("Todavía no se puede aprobar.", "COMPUERTA", faltas)
        vigente = _version_vigente(c, lote)
        _sellar(c, vigente)
        _decision(c, lote, vigente["id"], "aprobacion", "", usuario)
        _notificar(c, lote, "aprobado", f"{usuario.nombre} aprobó {lote['empresa']} {lote['periodo']} (v{vigente['numero']}).",
                   destinatario_id=lote["maker_id"])
        sello = _hash_aprobado(c, lote, vigente)
        ahora = ahora_utc()
        c.execute("UPDATE lote SET estado = 'aprobado', hash_aprobado = ?, aprobado_utc = ?, actualizado_utc = ? WHERE id = ?",
                  (sello, ahora, ahora, lote_id))
        auditoria.registrar(c, usuario, "aprobado", lote_id, vigente["id"], numero=vigente["numero"], hash_aprobado=sello)
        return _detalle(c, lote_id)


def anular(usuario: Usuario, lote_id: str, motivo: str) -> dict:
    _exigir_rol(usuario, "empleado", "supervisor")
    if len(motivo.strip()) < MOTIVO_MINIMO:
        raise ErrorDeLote(422, "MOTIVO", f"Contá por qué se anula (al menos {MOTIVO_MINIMO} caracteres).")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        _exigir_estado(lote, "borrador", "pendiente", "rechazado")
        _decision(c, lote, None, "anulacion", motivo.strip(), usuario)
        c.execute("UPDATE lote SET estado = 'anulado', actualizado_utc = ? WHERE id = ?", (ahora_utc(), lote_id))
        auditoria.registrar(c, usuario, "anulado", lote_id, None, motivo=motivo.strip())
        return _detalle(c, lote_id)


# ----------------------------------------------------------------------
# Exportación fiscal (solo aprobado)
# ----------------------------------------------------------------------


def _contenido_exportacion(tipo: str, lote: dict, partidas: list[dict]) -> bytes:
    if tipo == "libro_csv":
        salida = io.StringIO()
        escritor = csv.writer(salida, delimiter=";", lineterminator="\r\n")
        escritor.writerow(["Estado", "Código", "CUIT agente", "Razón social", "Impuesto", "Certificado", "Fecha fisco",
                           "Importe fisco", "Documento ERP", "Fecha ERP", "Importe ERP", "Diferencia", "Justificación", "Comentario",
                           "Importe ajustado", "Excluida"])
        for p in partidas:
            escritor.writerow([p["estado"], p["codigo"], p["cuit"], p["razon_social"], p["impuesto"], p["certificado"],
                               p["fecha_fisco"] or "", (p["importe_fisco"] or "").replace(".", ","), p["documento_erp"],
                               p["fecha_erp"] or "", (p["importe_erp"] or "").replace(".", ","), p["diferencia"].replace(".", ","),
                               P.JUSTIFICACIONES.get(p["justificacion"], ""), p["comentario"],
                               (p["importe_ajustado"] or "").replace(".", ","), "Sí" if p["excluida"] else ""])
        return ("﻿" + salida.getvalue()).encode("utf-8")
    # ddjj_txt: las retenciones que ARCA informa (son las computables en la DDJJ),
    # una por línea: CUIT|FECHA DDMMAAAA|CERTIFICADO|IMPUESTO|IMPORTE.
    # TODO: ajustar al formato exacto del aplicativo (SIFERE, IVA) cuando se defina.
    lineas = []
    for p in partidas:
        if not p["importe_fisco"] or p["excluida"]:
            continue
        fecha = p["fecha_fisco"] or ""
        lineas.append("|".join([p["cuit"].replace("-", ""), fecha[8:10] + fecha[5:7] + fecha[0:4], p["certificado"],
                                p["impuesto"], f"{Decimal(p['importe_fisco']):.2f}"]))
    return ("\r\n".join(lineas) + "\r\n").encode("utf-8")


def exportar(usuario: Usuario, lote_id: str, tipo: str) -> dict:
    if tipo not in EXPORTACIONES:
        raise ErrorDeLote(404, "NO_EXISTE", "Tipo de exportación desconocido.")
    with transaccion() as c:
        lote = _lote(c, lote_id)
        if lote["estado"] != "aprobado":
            raise _conflicto("La exportación fiscal se habilita cuando el lote está aprobado.")
        existente = c.execute("SELECT * FROM exportacion_fiscal WHERE lote_id = ? AND tipo = ?", (lote_id, tipo)).fetchone()
        if existente:
            return dict(existente)
        vigente = _version_vigente(c, lote)
        _, extension = EXPORTACIONES[tipo]
        contenido = _contenido_exportacion(tipo, lote, _partidas(c, vigente["id"]))
        nombre = f"{tipo} {lote['empresa']} {lote['periodo'].replace('/', '-')}.{extension}"
        exportacion = {"id": uuid.uuid4().hex[:12], "lote_id": lote_id, "tipo": tipo, "nombre": nombre,
                       "sha256": _guardar_archivo(contenido, f".{extension}"), "usuario_id": usuario.id,
                       "usuario_nombre": usuario.nombre, "fecha_utc": ahora_utc()}
        c.execute(f"INSERT INTO exportacion_fiscal ({', '.join(exportacion)}) VALUES ({', '.join('?' * len(exportacion))})",
                  list(exportacion.values()))
        auditoria.registrar(c, usuario, "exportacion", lote_id, vigente["id"], tipo=tipo, sha256=exportacion["sha256"])
        return exportacion


def archivo_exportacion(usuario: Usuario, lote_id: str, exportacion_id: str) -> tuple[Path, str]:
    with transaccion() as c:
        fila = c.execute("SELECT * FROM exportacion_fiscal WHERE id = ? AND lote_id = ?", (exportacion_id, lote_id)).fetchone()
        if fila is None:
            raise ErrorDeLote(404, "NO_EXISTE", "La exportación no existe.")
        auditoria.registrar(c, usuario, "descarga", lote_id, None, exportacion=exportacion_id, nombre=fila["nombre"])
        return ruta_archivo(fila["sha256"], "." + EXPORTACIONES[fila["tipo"]][1]), fila["nombre"]


# ----------------------------------------------------------------------
# Consultas
# ----------------------------------------------------------------------


def _detalle(c: sqlite3.Connection, lote_id: str) -> dict:
    lote = _lote(c, lote_id)
    versiones = [dict(v) for v in c.execute("SELECT * FROM lote_version WHERE lote_id = ? ORDER BY numero DESC", (lote_id,))]
    for v in versiones:
        v["resumen"] = json.loads(v.pop("resumen_json") or "{}")
        v["validacion"] = json.loads(v.pop("validacion_json") or "{}")
    vigente = next((v for v in versiones if v["numero"] == lote["version_vigente"]), None)
    partidas = _partidas(c, vigente["id"]) if vigente else []
    adjuntos = _adjuntos(c, lote_id)
    con_adjunto: dict[str, int] = {}
    for a in adjuntos:
        if a["clave_partida"]:
            con_adjunto[a["clave_partida"]] = con_adjunto.get(a["clave_partida"], 0) + 1
    for p in partidas:
        p["adjuntos"] = con_adjunto.get(p["clave"], 0)
        p["faltas"] = [m for _, m in V.requisitos(p, p["adjuntos"] > 0)]
    controles = {}
    if vigente:
        controles = {f["control"]: {"ok": bool(f["ok"]), "usuario": f["usuario_nombre"], "fecha": f["fecha_utc"]}
                     for f in c.execute("SELECT * FROM control_revision WHERE lote_id = ? AND version_id = ?", (lote_id, vigente["id"]))}
    numeros = {v["id"]: v["numero"] for v in versiones}
    observaciones = [{**dict(o), "version": numeros.get(o["version_id"])}
                     for o in c.execute("SELECT * FROM observacion WHERE lote_id = ? ORDER BY creado_utc", (lote_id,))]
    decisiones = [{**dict(d), "version": numeros.get(d["version_id"])}
                  for d in c.execute("SELECT * FROM decision WHERE lote_id = ? ORDER BY fecha_utc", (lote_id,))]
    abierto = lote["estado"] not in ("aprobado", "anulado")
    return {
        "lote": {**lote, "autores": sorted(_autores(c, lote))},
        "versiones": versiones,
        "partidas": partidas,
        "totales": P.totales_netos(partidas),
        "adjuntos": adjuntos,
        "controles": controles,
        "observaciones": observaciones,
        "decisiones": decisiones,
        "exportaciones": [dict(e) for e in c.execute("SELECT * FROM exportacion_fiscal WHERE lote_id = ?", (lote_id,))],
        "compuertas": {
            "enviar": compuertas(c, lote, "enviar") if abierto else [],
            "aprobar": compuertas(c, lote, "aprobar") if abierto else [],
        },
    }


def detalle(lote_id: str) -> dict:
    with transaccion(escritura=False) as c:
        return _detalle(c, lote_id)


def listar(estado: str | None = None, periodo: str | None = None, texto: str | None = None) -> list[dict]:
    condiciones, parametros = [], []
    if estado:
        condiciones.append("l.estado = ?")
        parametros.append(estado)
    if periodo:
        condiciones.append("l.periodo = ?")
        parametros.append(periodo)
    if texto:
        condiciones.append("(l.empresa LIKE ? OR l.cuit_empresa LIKE ? OR l.maker_nombre LIKE ? OR l.checker_nombre LIKE ?)")
        parametros += [f"%{texto}%"] * 4
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with transaccion(escritura=False) as c:
        filas = c.execute(
            f"""SELECT l.*, v.resumen_json, v.sellada_utc,
                   (SELECT GROUP_CONCAT(DISTINCT p.impuesto) FROM partida p WHERE p.version_id = v.id AND p.impuesto <> '') AS impuestos,
                   (SELECT COUNT(*) FROM observacion o WHERE o.lote_id = l.id AND o.resuelta_utc IS NULL) AS observaciones_abiertas,
                   (SELECT motivo FROM decision d WHERE d.lote_id = l.id AND d.tipo = 'rechazo' ORDER BY fecha_utc DESC LIMIT 1) AS ultimo_rechazo
                FROM lote l LEFT JOIN lote_version v ON v.lote_id = l.id AND v.numero = l.version_vigente
                {donde} ORDER BY l.actualizado_utc DESC""", parametros)
        lotes = []
        for f in filas:
            lote = dict(f)
            lote["resumen"] = json.loads(lote.pop("resumen_json") or "{}")
            lote["autores"] = sorted(_autores(c, lote))
            lotes.append(lote)
        return lotes


def diff(lote_id: str, desde: int, hasta: int) -> dict:
    with transaccion(escritura=False) as c:
        a, b = _version(c, lote_id, desde), _version(c, lote_id, hasta)
        pa = {p["clave"]: p for p in _partidas(c, a["id"])}
        pb = {p["clave"]: p for p in _partidas(c, b["id"])}
    campos = ("estado", "codigo", "importe_fisco", "importe_erp", "diferencia", "justificacion", "comentario",
              "importe_ajustado", "excluida")
    cambiadas = []
    for clave in pa.keys() & pb.keys():
        cambios = [{"campo": k, "antes": pa[clave][k], "despues": pb[clave][k]} for k in campos if pa[clave][k] != pb[clave][k]]
        if cambios:
            cambiadas.append({"clave": clave, "razon_social": pb[clave]["razon_social"], "cambios": cambios})
    return {
        "desde": desde, "hasta": hasta,
        "agregadas": [pb[k] for k in pb.keys() - pa.keys()],
        "quitadas": [pa[k] for k in pa.keys() - pb.keys()],
        "cambiadas": sorted(cambiadas, key=lambda x: pb[x["clave"]]["orden"]),
        "totales": {"desde": {"total_libro_erp": a["total_libro_erp"], "total_fisco": a["total_fisco"], **json.loads(a["resumen_json"])},
                    "hasta": {"total_libro_erp": b["total_libro_erp"], "total_fisco": b["total_fisco"], **json.loads(b["resumen_json"])}},
    }


def auditoria_de(lote_id: str) -> list[dict]:
    with transaccion(escritura=False) as c:
        _lote(c, lote_id)
        return auditoria.listar(c, lote_id=lote_id)


def notificaciones(usuario: Usuario) -> list[dict]:
    roles = set(usuario.roles)
    if "supervisor" in roles:
        roles.add("revisor")  # avisos guardados antes de pasar a dos roles
    roles = sorted(roles) or [""]
    with transaccion(escritura=False) as c:
        filas = c.execute(
            f"SELECT * FROM notificacion WHERE destinatario_id = ? OR destinatario_rol IN ({', '.join('?' * len(roles))}) "
            "ORDER BY creado_utc DESC LIMIT 50", (usuario.id, *roles))
        return [dict(f) for f in filas]


def marcar_leida(usuario: Usuario, notificacion_id: str) -> None:
    with transaccion() as c:
        c.execute("UPDATE notificacion SET leida_utc = ? WHERE id = ? AND leida_utc IS NULL", (ahora_utc(), notificacion_id))


def catalogo() -> dict:
    return {
        "estados_partida": list(P.ESTADOS), "codigos": P.CODIGOS, "flags": P.FLAGS, "justificaciones": P.JUSTIFICACIONES,
        "justificaciones_con_respaldo": list(P.JUSTIFICACIONES_CON_RESPALDO), "codigos_con_respaldo": list(P.CODIGOS_CON_RESPALDO),
        "controles": CONTROLES, "exportaciones": {k: v[0] for k, v in EXPORTACIONES.items()},
        "adjuntos_permitidos": sorted(ADJUNTOS_PERMITIDOS), "motivo_minimo": MOTIVO_MINIMO,
    }
