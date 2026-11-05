"""
Las hojas del lote dentro del papel de trabajo:

* CONCILIACION — una fila por partida con su estado y código. Protegida: solo
  Justificación y Comentario quedan para escribir. Totales al pie con fórmulas.
* _META        — muy oculta: lote, versión base y el hash de cada fila, para
  reconocer el archivo cuando vuelve subido.

Las hojas ARCA y LIBRO MAYOR (las del exportador) no se tocan.
"""

import io
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.services import partidas as P

HOJA = "CONCILIACION"
HOJA_META = "_META"
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


def _escribir_hojas(libro: Workbook, partidas: list[dict], meta: dict) -> None:
    for nombre in (HOJA, HOJA_META):
        if nombre in libro.sheetnames:
            del libro[nombre]
    hoja = libro.create_sheet(HOJA, 0)
    libro.active = 0
    negrita = Font(bold=True)
    for col, (titulo, _, ancho) in enumerate(COLUMNAS, start=1):
        celda = hoja.cell(row=1, column=col, value=titulo)
        celda.font, celda.fill = negrita, PatternFill("solid", fgColor="FFD9D9D9")
        hoja.column_dimensions[get_column_letter(col)].width = ancho
    hoja.freeze_panes = "B2"

    editables = {i for i, c in enumerate(COLUMNAS, start=1) if c[1] in P.CAMPOS_EDITABLES}
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


def a_texto(campo: str, valor) -> str | None:
    """Una celda leída, en la forma en que se guarda la partida."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None if campo in (*COLUMNAS_IMPORTE, *COLUMNAS_FECHA) else ""
    if campo in COLUMNAS_IMPORTE:
        return P.importe(valor)
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
