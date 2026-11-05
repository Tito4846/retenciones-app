"""
Pipeline de validación del Excel que vuelve subido (una versión nueva del lote).

Etapas en orden; dentro de cada una se juntan todos los errores para que el
analista corrija todo de una vez. Si una etapa tiene errores, las siguientes
no corren. La validación no escribe nada: devuelve el resultado y, si pasó,
las partidas de la versión nueva.

0 Transporte   tamaño, firma ZIP, sin macros, distinto de la versión vigente
1 Estructura   hojas, encabezados, lote y versión base de _META
2 Sintaxis     CUIT, fechas, importes y catálogos por fila
3 Integridad   duplicados, mismas filas que la base, columnas bloqueadas sin
               tocar, sin fórmulas fuera de los totales
4 Negocio      justificaciones y comentarios (advertencias: se exigen al enviar)
5 Cuadre       totales al centavo contra los de las fuentes
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


def requisitos(partida: dict, con_adjunto: bool) -> list[tuple[str, str]]:
    """Lo que le falta a una partida para poder enviarse: [(codigo, mensaje)]."""
    faltas = []
    if partida["estado"] == P.CONCILIADO:
        return faltas
    con_texto = bool((partida.get("comentario") or "").strip())
    if partida.get("excluida"):
        return [] if con_texto else [("SIN_COMENTARIO", "Falta el motivo por el que se quitó")]
    # Toda excepción lleva tipo, explicación y respaldo: no hay excepciones sin papeles.
    if not partida.get("justificacion"):
        faltas.append(("SIN_JUSTIFICACION", "Falta elegir el tipo de justificación"))
    if not con_texto:
        faltas.append(("SIN_COMENTARIO", "Falta escribir la justificación"))
    if not con_adjunto:
        faltas.append(("SIN_RESPALDO", "Falta el archivo de respaldo"))
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

    # 1) Estructura
    try:
        libro = load_workbook(io.BytesIO(contenido), data_only=False)
    except Exception:
        r.error(1, "ILEGIBLE", "No se pudo abrir el Excel.")
        r.corte(1)
        return r, []
    for nombre in (X.HOJA, X.HOJA_META):
        if nombre not in libro.sheetnames:
            r.error(1, "FALTA_HOJA", f"Falta la hoja {nombre}: subí el Excel que bajaste del lote, sin quitarle hojas.", hoja=nombre)
    if r.corte(1):
        return r, []
    hoja, meta_hoja = libro[X.HOJA], libro[X.HOJA_META]
    meta = {}
    for fila in meta_hoja.iter_rows(values_only=True):
        if fila and fila[0] == "clave":
            break
        if fila and fila[0]:
            meta[str(fila[0])] = fila[1]
    encabezados = [c.value for c in hoja[1]][: len(X.ENCABEZADOS)]
    if encabezados != X.ENCABEZADOS:
        r.error(1, "ENCABEZADOS", "Las columnas de la hoja CONCILIACION no son las originales (no se agregan, quitan ni mueven columnas).", hoja=X.HOJA, fila=1)
    if meta.get("formato") != X.FORMATO:
        r.error(1, "FORMATO", "El archivo no tiene el formato de un lote.", hoja=X.HOJA_META)
    if meta.get("lote_id") != lote["id"]:
        r.error(1, "OTRO_LOTE", "El archivo es de otro lote.", hoja=X.HOJA_META)
    elif str(meta.get("version")) != str(base["numero"]):
        r.error(1, "VERSION_VIEJA", f"Trabajaste sobre v{meta.get('version')} y la vigente es v{base['numero']}: bajá la vigente y volvé a cargar los cambios.", hoja=X.HOJA_META)
    if hoja.max_row > MAX_FILAS:
        r.error(1, "DEMASIADAS_FILAS", f"La hoja tiene más de {MAX_FILAS} filas.", hoja=X.HOJA)
    if r.corte(1):
        return r, []

    # Filas de datos: hasta la primera vacía (después vienen los totales).
    cantidad = int(meta.get("filas") or 0)
    leidas: list[tuple[int, dict]] = []
    for numero in range(2, hoja.max_row + 1):
        celdas = [hoja.cell(row=numero, column=c).value for c in range(1, len(X.COLUMNAS) + 1)]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in celdas):
            break
        if celdas[0] == X.TITULO_TOTALES:
            break
        leidas.append((numero, {campo: X.a_texto(campo, v) for (_, campo, _), v in zip(X.COLUMNAS, celdas, strict=True)}))

    # 2) Sintaxis por fila
    for numero, f in leidas:
        if f["estado"] not in P.ESTADOS:
            r.error(2, "ESTADO_INVALIDO", f"Estado desconocido: «{f['estado']}».", hoja=X.HOJA, fila=numero, columna="Estado")
        if f["codigo"] and f["codigo"] not in P.CODIGOS:
            r.error(2, "CODIGO_INVALIDO", f"Código desconocido: «{f['codigo']}».", hoja=X.HOJA, fila=numero, columna="Código")
        if f["justificacion"] and f["justificacion"] not in P.JUSTIFICACIONES:
            r.error(2, "JUSTIFICACION_INVALIDA", f"«{f['justificacion']}» no está en la lista de justificaciones.", hoja=X.HOJA, fila=numero, columna="Justificación")
        if f["cuit"] and not P.cuit_valida(f["cuit"]):
            r.aviso(2, "CUIT_INVALIDA", f"La CUIT {f['cuit']} tiene el dígito verificador mal.", hoja=X.HOJA, fila=numero, columna="CUIT")
        for campo in X.COLUMNAS_FECHA:
            valor = f[campo]
            if valor and len(valor) != 10:
                r.error(2, "FECHA_INVALIDA", f"«{valor}» no es una fecha.", hoja=X.HOJA, fila=numero, columna=_titulo(campo))
        for campo in X.COLUMNAS_IMPORTE:
            valor = f[campo]
            try:
                if valor is not None:
                    Decimal(valor)
            except InvalidOperation:
                r.error(2, "IMPORTE_INVALIDO", f"«{valor}» no es un importe.", hoja=X.HOJA, fila=numero, columna=_titulo(campo))
        if len(f["comentario"] or "") > 2000:
            r.error(2, "COMENTARIO_LARGO", "El comentario supera los 2000 caracteres.", hoja=X.HOJA, fila=numero, columna="Comentario")
    if r.corte(2):
        return r, []

    # 3) Integridad
    por_clave = {p["clave"]: p for p in partidas_base}
    vistas: dict[str, int] = {}
    for numero, f in leidas:
        clave = f["clave"]
        if clave in vistas:
            r.error(3, "DUPLICADA", f"La fila repite la partida de la fila {vistas[clave]}.", hoja=X.HOJA, fila=numero, columna="Clave")
            continue
        vistas[clave] = numero
        original = por_clave.get(clave)
        if original is None:
            r.error(3, "FILA_AGREGADA", "Fila que no está en el lote: las partidas se agregan re-conciliando, no a mano.", hoja=X.HOJA, fila=numero)
            continue
        if P.hash_fila(f) != original["hash_fila"]:
            for _, campo, _ in X.COLUMNAS:
                if campo in P.CAMPOS_BLOQUEADOS and (f[campo] or "") != (original[campo] or ""):
                    r.error(3, "COLUMNA_ALTERADA", f"Se modificó una columna bloqueada ({_titulo(campo)}).", hoja=X.HOJA,
                            fila=numero, columna=_titulo(campo), esperado=original[campo], encontrado=f[campo])
    for clave, original in por_clave.items():
        if clave not in vistas:
            r.error(3, "FILA_QUITADA", f"Falta la partida de {original['razon_social'] or original['cuit']} por {original['importe_fisco'] or original['importe_erp']}.",
                    hoja=X.HOJA, clave=clave)
    if cantidad != len(partidas_base):
        r.error(3, "META_ALTERADA", "La hoja _META no coincide con el lote.", hoja=X.HOJA_META)
    fila_totales = X.fila_de_totales(len(leidas))
    permitidas = {f"{X._letra(c)}{fila_totales}" for c in X.COLUMNAS_IMPORTE}
    for fila in hoja.iter_rows():
        for celda in fila:
            if celda.data_type == "f" and celda.coordinate not in permitidas:
                r.error(3, "FORMULA", "Hay una fórmula fuera de los totales: cargá valores.", hoja=X.HOJA, fila=celda.row, columna=celda.column_letter)
    if r.corte(3):
        return r, []

    nuevas = []
    for _, f in leidas:
        original = por_clave[f["clave"]]
        nuevas.append({**original, "justificacion": f["justificacion"] or "", "comentario": (f["comentario"] or "").strip()})

    # 4) Negocio (se exige al enviar; acá avisa)
    for numero, p in zip((n for n, _ in leidas), nuevas, strict=True):
        for codigo, mensaje in requisitos(p, con_adjunto=True):
            r.aviso(4, codigo, mensaje, hoja=X.HOJA, fila=numero, clave=p["clave"])

    # 5) Cuadre (recalculado acá: no se confía en los totales que trae el Excel)
    for e in cuadre(nuevas, base["total_libro_erp"], base["total_fisco"]):
        r.error(5, e["codigo"], e["mensaje"], hoja=X.HOJA, fila=fila_totales)
    r.corte(5)
    r.resumen = {**P.resumen(nuevas), "filas": len(nuevas),
                 "cambiadas": sum(1 for p in nuevas if (p["justificacion"], p["comentario"]) != (
                     por_clave[p["clave"]]["justificacion"], por_clave[p["clave"]]["comentario"]))}
    return r, nuevas if not r.errores else []


def _titulo(campo: str) -> str:
    return next(t for t, c, _ in X.COLUMNAS if c == campo)
