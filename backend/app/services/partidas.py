"""
Del resultado del cruce a las partidas del lote: una por retención, con el
estado inicial que pide la revisión, el código de la discrepancia y flags de
auditoría preliminar.

Estados (sobre los cuatro campos clave: CUIT, certificado, fecha e importe):

* Conciliado Automático   — par del mismo agente e importe, con la fecha dentro de
  la tolerancia. El certificado no hace falta: Xubio casi nunca lo carga en el asiento.
* Diferencia de Monto     — par con importes distintos (D01).
* Pendiente de Comprobante — par por un criterio débil: solo coinciden importe y
  fecha, sin nada que confirme al agente (P01), la CUIT no coincide (P02), la fecha
  está fuera de la tolerancia (P03) o lo emparejó una persona (P04).
* No Localizado en ERP    — está en ARCA y no en el Libro Mayor (N01).
* No Localizado en Fisco  — está en el Libro Mayor del período y no en ARCA (N02).
"""

import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal

from app.core.config import settings
from app.services.conciliador import CRITERIO_IMPORTE_FECHA, CRITERIO_MANUAL
from app.services.normalizacion import normalizar_arca, normalizar_comprobante, normalizar_mayor
from app.services.tools import dec, formatear_cuit, parse_fecha

CONCILIADO = "Conciliado Automático"
DIFERENCIA = "Diferencia de Monto"
PENDIENTE = "Pendiente de Comprobante"
NO_EN_ERP = "No Localizado en ERP"
NO_EN_FISCO = "No Localizado en Fisco"
ESTADOS = (CONCILIADO, DIFERENCIA, PENDIENTE, NO_EN_ERP, NO_EN_FISCO)

CODIGOS = {
    "D01": "Importe distinto entre ARCA y el asiento",
    "P01": "Coinciden importe y fecha, pero nada confirma que sea el mismo agente",
    "P02": "La CUIT del asiento no coincide con la de ARCA",
    "P03": "Fecha fuera de la tolerancia",
    "P04": "Emparejada a mano",
    "N01": "Informada en ARCA, sin registrar en el Libro Mayor",
    "N02": "Registrada en el Libro Mayor, ARCA no la informa",
}

FLAGS = {
    "DUP": "Posible duplicado (misma CUIT, certificado e importe)",
    "CUIT_INV": "CUIT con dígito verificador inválido",
    "FUERA_PER": "Fecha fuera del período",
    "SUG": "Hay una sugerencia de par sin aceptar",
}

# Cómo justifica el analista una partida que no concilió sola.
JUSTIFICACIONES = {
    "aceptada": "Aceptada sin ajuste",
    "temporal": "Diferencia temporal (se registra en otro período)",
    "ajuste": "Ajuste manual",
    "error_carga": "Error en la carga original",
}
# Estas exigen un archivo de respaldo (NC, certificado rectificativo, captura del error).
JUSTIFICACIONES_CON_RESPALDO = ("ajuste", "error_carga")
# Y estos códigos también: son ajustes manuales o diferencias de importe.
CODIGOS_CON_RESPALDO = ("D01", "P04")

# Las columnas que el analista no puede tocar (entran en el hash de la fila).
CAMPOS_BLOQUEADOS = ("clave", "estado", "codigo", "criterio", "cuit", "razon_social", "impuesto", "certificado",
                     "documento_erp", "fecha_fisco", "fecha_erp", "importe_fisco", "importe_erp", "diferencia")
CAMPOS_EDITABLES = ("justificacion", "comentario")
# Lo que se corrige en pantalla. Tampoco entra en hash_fila: las fuentes siguen intactas.
CAMPOS_EDICION = ("importe_ajustado", "excluida", "editado_por", "editado_rol", "editado_utc")


def cuit_valida(cuit: str | None) -> bool:
    digitos = "".join(c for c in str(cuit or "") if c.isdigit())
    if len(digitos) != 11:
        return False
    suma = sum(int(d) * p for d, p in zip(digitos[:10], (5, 4, 3, 2, 7, 6, 5, 4, 3, 2), strict=True))
    verificador = 11 - suma % 11
    verificador = {11: 0, 10: 9}.get(verificador, verificador)
    return verificador == int(digitos[10])


def importe(valor) -> str | None:
    """Importe como texto con dos decimales ('15000.00'), o None."""
    return None if valor is None or valor == "" else str(dec(valor))


def hash_fila(partida: dict) -> str:
    valores = [str(partida.get(c) or "") for c in CAMPOS_BLOQUEADOS]
    return hashlib.sha256("\x1f".join(valores).encode("utf-8")).hexdigest()[:32]


def _clave(lado: str, *partes) -> str:
    base = "|".join([lado, *(str(p or "") for p in partes)])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _indice(identificador: str) -> int:
    return int(identificador[1:])


def _estado_par(c: dict, r: dict, m: dict, tolerancia: int) -> tuple[str, str]:
    """(estado, código) de un par conciliado. r y m son las filas normalizadas de cada lado."""
    dias = c.get("diferencia_dias")
    if c["criterio"] == CRITERIO_MANUAL:
        return PENDIENTE, "P04"
    if dec(c["diferencia_importe"]) != 0:
        return DIFERENCIA, "D01"
    if r["cuit"] and m["cuit"] and r["cuit"] != m["cuit"]:
        return PENDIENTE, "P02"
    if dias is not None and dias > tolerancia:
        return PENDIENTE, "P03"
    if c["criterio"] == CRITERIO_IMPORTE_FECHA:
        return PENDIENTE, "P01"
    return CONCILIADO, ""


def marcar_exactos(resultado: dict, entrada) -> dict:
    """
    Anota en cada par por qué no concilió exacto (`observacion`, None si lo hizo) y
    devuelve cuántas partidas cuadran y cuántas va a tener que justificar el lote:
    son las mismas cuentas que `armar`, así el tablero y el lote dan igual.
    """
    tolerancia = resultado["resumen"].get("tolerancia_dias", settings.TOLERANCIA_DIAS)
    detalle = resultado["detalle"]
    mayor = [normalizar_mayor(m) for m in entrada.mayor.retenciones]
    arca = [normalizar_arca(r) for r in entrada.arca]
    exactas = 0
    for c in detalle["conciliados"]:
        estado, codigo = _estado_par(c, arca[_indice(c["id"])], mayor[_indice(c["id_mayor"])], tolerancia)
        c["observacion"] = CODIGOS.get(codigo)
        exactas += estado == CONCILIADO
    observadas = len(detalle["conciliados"]) - exactas
    return {"partidas_exactas": exactas,
            "partidas_a_revisar": observadas + len(detalle["faltan_en_mayor"]) + len(detalle["faltan_en_arca"])}


def armar(resultado: dict, entrada) -> list[dict]:
    """Las partidas (en el orden del Excel) a partir del cruce y lo leído de cada lado."""
    tolerancia = resultado["resumen"].get("tolerancia_dias", settings.TOLERANCIA_DIAS)
    hasta = parse_fecha(resultado["resumen"].get("hasta_periodo"))
    # Sin el inicio elegido (versiones viejas), el mes del final del período.
    desde = parse_fecha(resultado["resumen"].get("desde_periodo")) or (hasta.replace(day=1) if hasta else None)
    detalle = resultado["detalle"]
    mayor = [normalizar_mayor(m) for m in entrada.mayor.retenciones]
    arca = [normalizar_arca(r) for r in entrada.arca]
    sugeridos = {s.get("id_arca") for s in resultado.get("sugerencias", [])} | {
        s.get("id_mayor") for s in resultado.get("sugerencias", [])}

    def fuera_del_periodo(fecha: date | None) -> bool:
        if hasta is None or fecha is None:
            return False
        return not desde <= fecha <= hasta

    partidas: list[dict] = []
    for c in detalle["conciliados"]:
        r = arca[_indice(c["id"])]
        m = mayor[_indice(c["id_mayor"])]
        estado, codigo = _estado_par(c, r, m, tolerancia)
        partidas.append({
            "lado": "F", "id": c["id"], "estado": estado, "codigo": codigo, "criterio": c["criterio"],
            "cuit": c["cuit"], "razon_social": c["razon_social"], "impuesto": c.get("impuesto") or "",
            "certificado": c.get("certificado") or "", "documento_erp": c.get("documento_mayor") or "",
            "fecha_fisco": c["fecha"], "fecha_erp": c.get("fecha_mayor"),
            "importe_fisco": importe(c["importe"]), "importe_erp": importe(c["importe_mayor"]),
            "_fecha": r["fecha"], "_cuit": r["cuit"], "_cert": normalizar_comprobante(r.get("certificado")),
        })
    for f in detalle["faltan_en_mayor"]:
        r = arca[_indice(f["id"])]
        partidas.append({
            "lado": "F", "id": f["id"], "estado": NO_EN_ERP, "codigo": "N01", "criterio": "",
            "cuit": f["cuit"], "razon_social": f["razon_social"], "impuesto": f.get("impuesto") or "",
            "certificado": f.get("certificado") or "", "documento_erp": "",
            "fecha_fisco": f["fecha"], "fecha_erp": None, "importe_fisco": importe(f["importe"]), "importe_erp": None,
            "_fecha": r["fecha"], "_cuit": r["cuit"], "_cert": normalizar_comprobante(r.get("certificado")),
        })
    for f in detalle["faltan_en_arca"]:
        m = mayor[_indice(f["id"])]
        partidas.append({
            "lado": "E", "id": f["id"], "estado": NO_EN_FISCO, "codigo": "N02", "criterio": "",
            "cuit": f["cuit"], "razon_social": f["razon_social"], "impuesto": f.get("impuesto") or "",
            "certificado": "", "documento_erp": f.get("documento") or "",
            "fecha_fisco": None, "fecha_erp": f["fecha"], "importe_fisco": None, "importe_erp": importe(f["importe"]),
            "_fecha": m["fecha"], "_cuit": m["cuit"], "_cert": normalizar_comprobante(m.get("documento")),
        })

    # Flags y clave estable (para seguir la partida entre versiones).
    repetidas = Counter((p["lado"], p["_cuit"], p["_cert"], p["importe_fisco"] or p["importe_erp"])
                        for p in partidas if p["_cert"])
    vistas: Counter = Counter()
    for orden, p in enumerate(partidas, start=1):
        flags = []
        if p["_cert"] and repetidas[(p["lado"], p["_cuit"], p["_cert"], p["importe_fisco"] or p["importe_erp"])] > 1:
            flags.append("DUP")
        if p["_cuit"] and not cuit_valida(p["_cuit"]):
            flags.append("CUIT_INV")
        if fuera_del_periodo(p["_fecha"]):
            flags.append("FUERA_PER")
        if p["id"] in sugeridos:
            flags.append("SUG")
        base = (p["lado"], p["_cuit"], p["_cert"], p["_fecha"], p["importe_fisco"] or p["importe_erp"])
        vistas[base] += 1
        p["clave"] = _clave(*base, vistas[base])
        p["orden"] = orden
        p["flags"] = flags
        p["cuit"] = p["cuit"] or formatear_cuit(p["_cuit"])
        fisco, erp = Decimal(p["importe_fisco"] or "0"), Decimal(p["importe_erp"] or "0")
        p["diferencia"] = str(fisco - erp)
        p["justificacion"] = ""
        p["comentario"] = ""
        p.update(importe_ajustado=None, excluida=0, editado_por="", editado_rol="", editado_utc=None)
        for privado in ("_fecha", "_cuit", "_cert", "lado", "id"):
            p.pop(privado)
        p["hash_fila"] = hash_fila(p)
    return partidas


def totales_fuente(resultado: dict, entrada) -> tuple[str, str]:
    """
    (total del libro de retenciones del período, total informado por ARCA),
    calculados desde lo leído de cada lado y no desde la clasificación: si el
    cruce perdiera una fila, el cuadre no cierra.
    """
    detalle = resultado["detalle"]
    usados = {c["id_mayor"] for c in detalle["conciliados"]} | {f["id"] for f in detalle["faltan_en_arca"]}
    total_mayor = Decimal("0.00")
    hasta = parse_fecha(resultado["resumen"].get("hasta_periodo"))
    for j, m in enumerate(entrada.mayor.retenciones):
        fecha = parse_fecha(m.get("fecha"))
        # Los días de más que se piden para atrapar asientos con fecha del cobro
        # no son del período, salvo que se hayan emparejado con algo del período.
        if f"m{j}" in usados or hasta is None or fecha is None or fecha <= hasta:
            total_mayor += dec(normalizar_mayor(m)["importe"])
    total_arca = sum((dec(normalizar_arca(r)["importe"]) for r in entrada.arca), Decimal("0.00"))
    return str(total_mayor), str(total_arca)


def totales_netos(partidas: list[dict]) -> dict:
    """
    Lo que queda para conciliar después de los ajustes. Sistema es el Libro Mayor
    (o el importe ajustado, si lo hay) y declarado es ARCA. Las partidas excluidas
    no cuentan en ninguno de los dos lados.
    """
    sistema, declarado = Decimal("0.00"), Decimal("0.00")
    for p in partidas:
        if p.get("excluida"):
            continue
        sistema += Decimal(p.get("importe_ajustado") or p["importe_erp"] or "0")
        declarado += Decimal(p["importe_fisco"] or "0")
    return {"sistema": str(sistema), "declarado": str(declarado), "neta": str(declarado - sistema)}


def resumen(partidas: list[dict]) -> dict:
    por_estado = Counter(p["estado"] for p in partidas)
    netos = totales_netos(partidas)
    return {
        "importe_sistema": netos["sistema"],
        "importe_declarado": netos["declarado"],
        "partidas": len(partidas),
        "excepciones": sum(1 for p in partidas if p["estado"] != CONCILIADO and not p.get("excluida")),
        "por_estado": {e: por_estado.get(e, 0) for e in ESTADOS},
        "importe_fisco": str(sum((Decimal(p["importe_fisco"] or "0") for p in partidas), Decimal("0.00"))),
        "importe_erp": str(sum((Decimal(p["importe_erp"] or "0") for p in partidas), Decimal("0.00"))),
    }


def a_json(partida: dict) -> dict:
    """La partida como la devuelve la API (flags como lista)."""
    datos = dict(partida)
    if isinstance(datos.get("flags"), str):
        datos["flags"] = json.loads(datos["flags"] or "[]")
    return datos
