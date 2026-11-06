"""
Lectura del Excel que vuelve subido (una versión nueva del lote).

El Excel es interno y solo trae trabajo: de cada fila, buscada por su Clave, se
toman las columnas libres (Justificación, Comentario, Importe ajustado y
Excluida). Todo lo demás sale de la versión vigente, así que no hace falta
vigilarlo: lo que se haya tocado afuera se ignora y se avisa, sin frenar la carga.

Se rechaza solo lo que impide usar el archivo:

0 Archivo   tamaño, firma ZIP, sin macros, distinto de la versión vigente
1 Lote      hoja CONCILIACION con su columna Clave, y que sea de este lote

Lo que falte completar no frena nada: lo evalúa quien aprueba el lote.
"""

import hashlib
import io
import zipfile
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.core.config import settings
from app.services import excel_lote as X
from app.services import partidas as P

MAX_FILAS = 50_000


class Resultado:
    def __init__(self) -> None:
        self.errores: list[dict] = []
        self.advertencias: list[dict] = []
        self.etapa_fallida: int | None = None
        self.resumen: dict = {}

    def error(self, etapa: int, codigo: str, mensaje: str, **donde) -> None:
        self.errores.append({"etapa": etapa, "codigo": codigo, "mensaje": mensaje, **donde})

    def aviso(self, etapa: int, codigo: str, mensaje: str, **donde) -> None:
        self.advertencias.append({"etapa": etapa, "codigo": codigo, "mensaje": mensaje, **donde})

    def corte(self, etapa: int) -> bool:
        if any(e["etapa"] == etapa for e in self.errores):
            self.etapa_fallida = etapa
            return True
        return False

    def a_dict(self) -> dict:
        return {"valida": not self.errores, "etapa_fallida": self.etapa_fallida, "errores": self.errores,
                "advertencias": self.advertencias, "resumen": self.resumen}


def requisitos(partida: dict) -> list[tuple[str, str]]:
    """Lo que le falta a una partida para poder enviarse: [(codigo, mensaje)]."""
    faltas = []
    if partida["estado"] == P.CONCILIADO:
        return faltas
    con_texto = bool((partida.get("comentario") or "").strip())
    if partida.get("excluida"):
        return [] if con_texto else [("SIN_COMENTARIO", "Falta el motivo por el que se quitó")]
    # Los comprobantes son del lote entero: esa compuerta está en lotes.compuertas.
    if not partida.get("justificacion"):
        faltas.append(("SIN_JUSTIFICACION", "Falta elegir el tipo de justificación"))
    if not con_texto:
        faltas.append(("SIN_COMENTARIO", "Falta escribir la justificación"))
    return faltas


def cuadre(partidas: list[dict], total_libro_erp: str, total_fisco: str) -> list[dict]:
    """Errores de cuadre al centavo (vacío = cuadra)."""
    errores = []
    for lado, campo, esperado in (("ERP", "importe_erp", total_libro_erp), ("Fisco", "importe_fisco", total_fisco)):
        suma = sum((Decimal(p[campo] or "0") for p in partidas), Decimal("0.00"))
        if suma != Decimal(esperado):
            errores.append({"codigo": f"CUADRE_{lado.upper()}", "mensaje": (
                f"Los importes {lado} suman {suma} y el total de la fuente es {esperado} "
                f"(diferencia {suma - Decimal(esperado)}).")})
    return errores


def validar(contenido: bytes, lote: dict, base: dict, partidas_base: list[dict]) -> tuple[Resultado, list[dict]]:
    """`base`: la versión vigente (la que se bajó para trabajar). Devuelve (resultado, partidas nuevas)."""
    r = Resultado()

    # 0) Transporte
    if len(contenido) > settings.MAX_MB_ARCHIVO * 1024 * 1024:
        r.error(0, "ARCHIVO_GRANDE", f"El archivo supera los {settings.MAX_MB_ARCHIVO} MB.")
    if not contenido.startswith(b"PK"):
        r.error(0, "NO_ES_XLSX", "El archivo no es un Excel .xlsx.")
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                nombres = z.namelist()
            if "[Content_Types].xml" not in nombres:
                r.error(0, "NO_ES_XLSX", "El archivo no es un Excel .xlsx.")
            if any("vbaProject" in n for n in nombres):
                r.error(0, "CON_MACROS", "El archivo tiene macros: guardalo como .xlsx sin macros.")
        except zipfile.BadZipFile:
            r.error(0, "NO_ES_XLSX", "El archivo está dañado o no es un .xlsx.")
    if hashlib.sha256(contenido).hexdigest() == base["sha256"]:
        r.error(0, "SIN_CAMBIOS", f"Es el mismo archivo que la versión v{base['numero']}: no hay nada nuevo para subir.")
    if r.corte(0):
        return r, []

    # 1) Que sea de este lote
    try:
        libro = load_workbook(io.BytesIO(contenido), data_only=True)
    except Exception:
        r.error(1, "ILEGIBLE", "No se pudo abrir el Excel.")
        r.corte(1)
        return r, []
    if X.HOJA not in libro.sheetnames:
        r.error(1, "FALTA_HOJA", f"No encontré la hoja {X.HOJA}: subí el Excel que bajaste de este lote.", hoja=X.HOJA)
        r.corte(1)
        return r, []
    hoja = libro[X.HOJA]
    if X.HOJA_META in libro.sheetnames:
        meta = _meta(libro[X.HOJA_META])
        if meta.get("lote_id") not in (None, lote["id"]):
            r.error(1, "OTRO_LOTE", "El archivo es de otro lote.", hoja=X.HOJA_META)
        elif meta.get("version") not in (None, base["numero"], str(base["numero"])):
            r.aviso(1, "VERSION_VIEJA", f"El archivo salió de la v{meta['version']} y la vigente era la v{base['numero']}: "
                    "se aplicó lo que traía.", hoja=X.HOJA_META)
    if hoja.max_row > MAX_FILAS:
        r.error(1, "DEMASIADAS_FILAS", f"La hoja tiene más de {MAX_FILAS} filas.", hoja=X.HOJA)
    posicion = {c.value: c.column for c in hoja[1] if c.value}
    if "Clave" not in posicion:
        r.error(1, "SIN_CLAVE", "Falta la columna «Clave»: es la que identifica cada partida.", hoja=X.HOJA, columna="Clave")
    if r.corte(1):
        return r, []

    # 2) Las filas: en cualquier orden, y las que no vengan quedan como estaban
    por_clave = {p["clave"]: p for p in partidas_base}
    leidas: dict[str, dict] = {}
    numeros: dict[str, int] = {}
    for numero in range(2, hoja.max_row + 1):
        clave = hoja.cell(row=numero, column=posicion["Clave"]).value
        clave = str(clave).strip() if clave is not None else ""
        if not clave or clave == X.TITULO_TOTALES:
            continue
        original = por_clave.get(clave)
        if original is None:
            r.aviso(2, "FILA_AGREGADA", f"Se ignoró la fila {numero}: no es una partida del lote.", hoja=X.HOJA, fila=numero)
            continue
        if clave in leidas:
            r.aviso(2, "DUPLICADA", f"Se ignoró la fila {numero}: repite la fila {numeros[clave]}.", hoja=X.HOJA, fila=numero)
            continue
        fila = {campo: X.a_texto(campo, hoja.cell(row=numero, column=posicion[titulo]).value)
                for titulo, campo, _ in X.COLUMNAS if titulo in posicion}
        leidas[clave], numeros[clave] = fila, numero
        _revisar_ajuste(r, fila, numero)
        tocadas = [_titulo(c) for c in P.CAMPOS_BLOQUEADOS if c in fila and (fila[c] or "") != (original[c] or "")]
        if tocadas:
            r.aviso(2, "COLUMNA_ALTERADA", f"Fila {numero}: se mantuvo el dato original de {_lista(tocadas)}.", hoja=X.HOJA, fila=numero)
    if not leidas:
        r.error(1, "OTRO_LOTE", "El archivo no tiene ninguna partida de este lote.", hoja=X.HOJA)
    if r.corte(1):
        return r, []
    if faltantes := len(por_clave) - len(leidas):
        r.aviso(2, "FILA_QUITADA", f"{_cantidad(faltantes)} no venía en el Excel: quedó como estaba.", hoja=X.HOJA)

    nuevas = [_aplicar(p, leidas.get(p["clave"]), numeros.get(p["clave"]), r) for p in partidas_base]

    # No frena: lo evalúa quien aprueba.
    for p in nuevas:
        for codigo, mensaje in requisitos(p):
            r.aviso(4, codigo, mensaje, hoja=X.HOJA, fila=numeros.get(p["clave"]), clave=p["clave"])

    r.resumen = {**P.resumen(nuevas), "filas": len(nuevas),
                 "cambiadas": sum(1 for p in nuevas if any(p[k] != por_clave[p["clave"]][k] for k in X.COLUMNAS_LIBRES))}
    return r, nuevas


def _meta(hoja) -> dict:
    meta = {}
    for fila in hoja.iter_rows(values_only=True):
        if fila and fila[0] == "clave":
            break
        if fila and fila[0]:
            meta[str(fila[0])] = fila[1]
    return meta


def _revisar_ajuste(r: Resultado, fila: dict, numero: int) -> None:
    """Con el ajuste se hacen cuentas: si no es un número se ignora y queda el que estaba."""
    valor = fila.get("importe_ajustado")
    if valor is None:
        return
    try:
        Decimal(valor)
    except InvalidOperation:
        del fila["importe_ajustado"]
        r.aviso(2, "IMPORTE_INVALIDO", f"Fila {numero}: se ignoró el Importe ajustado «{valor}», no es un número.",
                hoja=X.HOJA, fila=numero, columna="Importe ajustado")


def _aplicar(partida: dict, fila: dict | None, numero: int | None, r: Resultado) -> dict:
    """La partida guardada con lo que trajo el Excel en las columnas libres."""
    nueva = {**partida}
    if fila is None:
        return nueva
    for campo in P.CAMPOS_EDITABLES:
        if campo in fila:
            nueva[campo] = (fila[campo] or "").strip()
    cambios = {}
    if "importe_ajustado" in fila:
        cambios["importe_ajustado"] = fila["importe_ajustado"]
    if "excluida" in fila:
        cambios["excluida"] = int((fila["excluida"] or "").lower() not in ("", "no", "0"))
    if partida["estado"] != P.CONCILIADO:
        return {**nueva, **cambios}
    if any(partida[k] != v for k, v in cambios.items()):
        r.aviso(2, "CONCILIADA", f"Fila {numero}: concilió sola, así que no se ajustó ni se excluyó.", hoja=X.HOJA, fila=numero)
    return nueva


def _lista(titulos: list[str]) -> str:
    return ", ".join(f"«{t}»" for t in titulos)


def _cantidad(n: int) -> str:
    return "1 partida" if n == 1 else f"{n} partidas"


def _titulo(campo: str) -> str:
    return next(t for t, c, _ in X.COLUMNAS if c == campo)
