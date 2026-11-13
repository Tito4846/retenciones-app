"""
Las hojas del lote dentro del papel de trabajo:

* RESUMEN      — la vista general: totales, cuánto concilió solo y qué falta resolver.
* CONCILIACION — una fila por partida con su estado y código, primero las que
  hay que mirar. Protegida: solo Justificación, Comentario, Importe ajustado y
  Excluida quedan para escribir. Totales al pie con fórmulas.
* _META        — muy oculta: lote, versión base y el hash de cada fila, para
  reconocer el archivo cuando vuelve subido.

Las hojas ARCA y LIBRO MAYOR (las del exportador) no se tocan.
"""

import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.services import partidas as P

HOJA = "CONCILIACION"
HOJA_META = "_META"
HOJA_RESUMEN = "RESUMEN"
FORMATO = "lote-v1"

# (encabezado, campo, ancho)
COLUMNAS = [
    ("Clave", "clave", 18), ("Estado", "estado", 24), ("Código", "codigo", 8), ("Flags", "flags", 14),
    ("Criterio", "criterio", 16), ("CUIT", "cuit", 15), ("Razón social", "razon_social", 34),
    ("Impuesto", "impuesto", 12), ("Certificado", "certificado", 18), ("Documento ERP", "documento_erp", 20),
    ("Fecha Fisco", "fecha_fisco", 12), ("Fecha ERP", "fecha_erp", 12), ("Importe Fisco", "importe_fisco", 15),
    ("Importe ERP", "importe_erp", 15), ("Diferencia", "diferencia", 13), ("Justificación", "justificacion", 20),
    ("Comentario", "comentario", 44), ("Importe ajustado", "importe_ajustado", 15), ("Excluida", "excluida", 9),
]
ENCABEZADOS = [c[0] for c in COLUMNAS]
CAMPO = {c[0]: c[1] for c in COLUMNAS}
COLUMNAS_IMPORTE = ("importe_fisco", "importe_erp", "diferencia")
COLUMNAS_FECHA = ("fecha_fisco", "fecha_erp")
COLUMNAS_LIBRES = (*P.CAMPOS_EDITABLES, "importe_ajustado", "excluida")
TITULO_TOTALES = "TOTALES"
CLAVE_PROTECCION = "retenciones"

_COLOR = {
    P.CONCILIADO: "FFE2F0D9", P.DIFERENCIA: "FFFCE4D6", P.PENDIENTE: "FFFFF2CC",
    P.NO_EN_ERP: "FFF8CBAD", P.NO_EN_FISCO: "FFDDEBF7",
}


def _letra(campo: str) -> str:
    return get_column_letter(next(i for i, c in enumerate(COLUMNAS, start=1) if c[1] == campo))


def fila_de_totales(cantidad: int) -> int:
    return cantidad + 3


def _valor(campo: str, partida: dict):
    valor = partida.get(campo)
    if campo == "flags":
        return ", ".join(valor if isinstance(valor, list) else P.a_json(partida)["flags"])
    if campo in COLUMNAS_IMPORTE or campo == "importe_ajustado":
        return float(valor) if valor not in (None, "") else None
    if campo == "excluida":
        return "Sí" if valor else None
    if campo in COLUMNAS_FECHA:
        return date.fromisoformat(valor) if valor else None
    if campo == "justificacion":
        return P.JUSTIFICACIONES.get(valor, valor) or None
    return valor or None


def _escribir_resumen(libro: Workbook, partidas: list[dict], meta: dict) -> None:
    """La primera hoja que se ve: se lee sola, sin recorrer la conciliación."""
    hoja = libro.create_sheet(HOJA_RESUMEN, 0)
    negrita = Font(bold=True)
    gris = PatternFill("solid", fgColor="FFD9D9D9")
    for columna, ancho in zip("ABCDE", (30, 34, 14, 18, 52), strict=True):
        hoja.column_dimensions[columna].width = ancho

    def fila(*valores, titulo: bool = False) -> None:
        hoja.append(list(valores))
        for celda in hoja[hoja.max_row]:
            if titulo:
                celda.font, celda.fill = negrita, gris
            if isinstance(celda.value, float):
                celda.number_format = "#,##0.00"

    pendientes = [p for p in partidas if p["estado"] != P.CONCILIADO and not p.get("excluida")]
    netos = P.totales_netos(partidas)
    por_estado = P.resumen(partidas)["por_estado"]
    hoja.append([f"{meta.get('empresa', '')} · {meta.get('periodo', '')} · versión {meta.get('version', '')}"])
    hoja["A1"].font = Font(bold=True, size=14)
    hoja.append(["Conciliaron solas todas las partidas." if not pendientes else
                 f"Conciliaron solas {len(partidas) - len(pendientes)} de {len(partidas)} partidas. "
                 f"Quedan {len(pendientes)} para resolver en la hoja {HOJA}."])
    hoja.append([])
    fila("Totales", "Importe", titulo=True)
    fila("Informado en ARCA", float(netos["declarado"]))
    fila("Registrado en el Mayor", float(netos["sistema"]))
    fila("Diferencia", float(netos["neta"]))
    hoja.cell(row=hoja.max_row, column=1).font = negrita
    hoja.cell(row=hoja.max_row, column=2).font = negrita
    hoja.append([])
    fila("Estado", "Partidas", titulo=True)
    for estado, cantidad in por_estado.items():
        fila(estado, cantidad)
    if pendientes:
        hoja.append([])
        fila("Para resolver", "Razón social", "Impuesto", "Diferencia", "Por qué", titulo=True)
        for p in pendientes:
            fila(p["estado"], p["razon_social"] or None, p["impuesto"] or None, float(p["diferencia"]), P.CODIGOS.get(p["codigo"]))
    hoja.protection.sheet = True
    hoja.protection.password = CLAVE_PROTECCION


def _escribir_hojas(libro: Workbook, partidas: list[dict], meta: dict) -> None:
    for nombre in (HOJA, HOJA_META, HOJA_RESUMEN):
        if nombre in libro.sheetnames:
            del libro[nombre]
    hoja = libro.create_sheet(HOJA, 0)
    _escribir_resumen(libro, partidas, meta)
    libro.active = 0
    # Lo que hay que mirar va arriba; sorted conserva el orden dentro de cada grupo.
    partidas = sorted(partidas, key=lambda p: p["estado"] == P.CONCILIADO)
    negrita = Font(bold=True)
    for col, (titulo, _, ancho) in enumerate(COLUMNAS, start=1):
        celda = hoja.cell(row=1, column=col, value=titulo)
        celda.font, celda.fill = negrita, PatternFill("solid", fgColor="FFD9D9D9")
        hoja.column_dimensions[get_column_letter(col)].width = ancho
    hoja.freeze_panes = "B2"

    editables = {i for i, c in enumerate(COLUMNAS, start=1) if c[1] in COLUMNAS_LIBRES}
    for f, partida in enumerate(partidas, start=2):
        relleno = PatternFill("solid", fgColor=_COLOR.get(partida["estado"], "FFFFFFFF"))
        for col, (_, campo, _) in enumerate(COLUMNAS, start=1):
            celda = hoja.cell(row=f, column=col, value=_valor(campo, partida))
            if campo in COLUMNAS_IMPORTE or campo == "importe_ajustado":
                celda.number_format = "#,##0.00"
            elif campo in COLUMNAS_FECHA:
                celda.number_format = "DD/MM/YYYY"
            if col in editables:
                celda.protection = Protection(locked=False)
                celda.alignment = Alignment(wrap_text=True, vertical="top")
            elif campo == "estado":
                celda.fill = relleno

    ultima = len(partidas) + 1
    total = fila_de_totales(len(partidas))
    hoja.cell(row=total, column=1, value=TITULO_TOTALES).font = negrita
    for campo in COLUMNAS_IMPORTE:
        letra = _letra(campo)
        celda = hoja[f"{letra}{total}"]
        celda.value = f"=SUM({letra}2:{letra}{max(ultima, 2)})"
        celda.font, celda.number_format = negrita, "#,##0.00"

    lista = DataValidation(type="list", formula1='"' + ",".join(P.JUSTIFICACIONES.values()) + '"', allow_blank=True)
    lista.error, lista.errorTitle = "Elegí una justificación de la lista.", "Justificación"
    hoja.add_data_validation(lista)
    if partidas:
        lista.add(f"{_letra('justificacion')}2:{_letra('justificacion')}{ultima}")
    excluida = DataValidation(type="list", formula1='"Sí"', allow_blank=True)
    excluida.error, excluida.errorTitle = "Escribí Sí para quitarla, o dejala vacía.", "Excluida"
    hoja.add_data_validation(excluida)
    if partidas:
        excluida.add(f"{_letra('excluida')}2:{_letra('excluida')}{ultima}")
    hoja.protection.sheet = True
    hoja.protection.password = CLAVE_PROTECCION
    hoja.protection.formatColumns = False
    hoja.protection.autoFilter = False
    hoja.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNAS))}{ultima}"

    datos = libro.create_sheet(HOJA_META)
    datos.sheet_state = "veryHidden"
    for clave, valor in {"formato": FORMATO, **meta, "filas": len(partidas)}.items():
        datos.append([clave, valor])
    datos.append([])
    datos.append(["clave", "hash_fila"])
    for partida in partidas:
        datos.append([partida["clave"], partida["hash_fila"]])
    datos.protection.sheet = True
    datos.protection.password = CLAVE_PROTECCION


def generar(base: bytes, partidas: list[dict], meta: dict) -> bytes:
    """El papel de trabajo `base` (ARCA + LIBRO MAYOR) con las hojas del lote."""
    libro = load_workbook(io.BytesIO(base))
    _escribir_hojas(libro, partidas, meta)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def tiene_letras(valor) -> bool:
    return isinstance(valor, str) and any(c.isalpha() for c in valor)


def _legible(valor) -> bool:
    try:
        return Decimal(str(valor).strip()).is_finite()
    except InvalidOperation:
        return False


def a_texto(campo: str, valor) -> str | None:
    """Una celda leída, en la forma en que se guarda la partida."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None if campo in (*COLUMNAS_IMPORTE, *COLUMNAS_FECHA, "importe_ajustado") else ""
    if campo in (*COLUMNAS_IMPORTE, "importe_ajustado"):
        # Lo ilegible queda tal cual para que la validación lo marque: dec() lo volvería 0.00.
        return P.importe(str(valor).strip()) if _legible(valor) else str(valor).strip()
    if campo in COLUMNAS_FECHA:
        if isinstance(valor, datetime):
            return valor.date().isoformat()
        if isinstance(valor, date):
            return valor.isoformat()
        from app.services.tools import parse_fecha

        fecha = parse_fecha(valor)
        return fecha.isoformat() if fecha else str(valor)
    if campo == "justificacion":
        texto = str(valor).strip()
        return next((k for k, v in P.JUSTIFICACIONES.items() if v == texto or k == texto), texto)
    if campo == "flags":
        return str(valor)
    return str(valor).strip()
