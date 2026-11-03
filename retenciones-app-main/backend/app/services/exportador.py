"""
Arma el papel de trabajo de la conciliacion de retenciones.

Toma los estilos y utilidades del exportador del FO 02-03 (encabezado celeste,
amarillo = tiene coincidencia, estado en texto, formato de importe contable,
logo en A1:B3) para que los dos papeles se lean igual. A diferencia del
bancario no hay plantilla de auditoria: el membrete se escribe aca.

Hojas:
    Resumen            A/B/C con cantidad y monto, los primeros casos de B y C
                       y los totales por impuesto.
    Conciliados        A: ARCA + asiento del Libro Mayor, con la clave del cruce.
    Faltan_en_ARCA     B: registradas en el Libro Mayor de Xubio sin certificado en ARCA (reclamo).
    Faltan_en_Mayor      C: declaradas en ARCA sin registrar en el Libro Mayor de Xubio (ajuste).
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ImagenExcel
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.worksheet import Worksheet

from app.core.config import settings

logger = logging.getLogger(__name__)

ANCHO_LOGO_MAX = 118  # px, entra en A:B con un margen
ALTO_LOGO_MAX = 78    # px, entra en las filas 1-3

FORMATO_IMPORTE = '_ * #,##0.00_ ;_ * \\-#,##0.00_ ;_ * "-"??_ ;_ @_ '
FORMATO_FECHA = "dd/mm/yyyy"

FILA_TITULO = 2
FILA_ENCABEZADO = 8
FILA_PRIMER_DATO = 9

RELLENO_ENCABEZADO = PatternFill("solid", fgColor="FF00B0F0")
RELLENO_CONCILIADO = PatternFill("solid", fgColor="FFFFFF00")  # amarillo: tiene coincidencia
FUENTE_ENCABEZADO = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
FUENTE_TITULO = Font(name="Calibri", size=14, bold=True)
FUENTE_NEGRITA = Font(name="Calibri", size=11, bold=True)
FUENTE_NORMAL = Font(name="Calibri", size=11)
ALINEACION_CENTRO = Alignment(horizontal="center", vertical="center")
ALINEACION_DERECHA = Alignment(horizontal="right", shrink_to_fit=True)
BORDE_DOBLE = Border(top=Side(style="thin"), bottom=Side(style="double"))

# Casos de B y C que se muestran en el resumen (el detalle completo, en su hoja).
CASOS_EN_RESUMEN = 10

ESTADO_CONCILIADO = "Conciliado"
ESTADO_SIN_COINCIDENCIA = "Sin coincidencia"

ETIQUETAS_CRITERIO = {
    "comprobante": "Nro. de comprobante",
    "cuit_importe": "CUIT + importe",
    "razon_social": "Razón social + importe + fecha",
    "importe_fecha": "Importe + fecha",
    "diferencia_menor": "Mismo agente, centavos de diferencia",
    "manual": "Conciliado a mano",
}


# ----------------------------------------------------------------------
# Utilidades (mismas que el exportador del FO 02-03)
# ----------------------------------------------------------------------

def _fecha(valor: object) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and valor.strip():
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(valor.strip()[:10], fmt).date()
            except ValueError:
                continue
    return None


def _importe(valor: object) -> float:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if n != n or n in (float("inf"), float("-inf")):
        return 0.0
    return round(n, 2)


def _escribir_fila(
    hoja: Worksheet,
    fila: int,
    valores: list,
    columnas_importe: set[int] = frozenset(),
    fuente: Font | None = None,
    relleno: PatternFill | None = None,
) -> None:
    for i, v in enumerate(valores):
        celda = hoja.cell(row=fila, column=1 + i, value=v)
        if i in columnas_importe:
            celda.number_format = FORMATO_IMPORTE
            celda.alignment = ALINEACION_DERECHA
        elif isinstance(v, date):
            celda.number_format = FORMATO_FECHA
            celda.alignment = ALINEACION_CENTRO
        celda.font = fuente or FUENTE_NORMAL
        if relleno:
            celda.fill = relleno


def _escribir_encabezados(hoja: Worksheet, fila: int, columnas: list[str]) -> None:
    for i, nombre in enumerate(columnas, start=1):
        celda = hoja.cell(row=fila, column=i, value=nombre)
        celda.font = FUENTE_ENCABEZADO
        celda.fill = RELLENO_ENCABEZADO
        celda.alignment = ALINEACION_CENTRO


def _logo_escalado(ruta: Path) -> tuple[io.BytesIO, int, int]:
    """El logo redimensionado (sin deformar) para el recuadro A1:B3."""
    from PIL import Image as ImagenPIL

    with ImagenPIL.open(ruta) as imagen:
        ancho, alto = imagen.size
        escala = min(ANCHO_LOGO_MAX / ancho, ALTO_LOGO_MAX / alto)
        nuevo = (max(1, round(ancho * escala)), max(1, round(alto * escala)))
        buffer = io.BytesIO()
        imagen.convert("RGBA").resize(nuevo, ImagenPIL.LANCZOS).save(buffer, format="PNG")
        buffer.seek(0)
        return buffer, nuevo[0], nuevo[1]


def _insertar_logo(hoja: Worksheet) -> None:
    """Si falta el logo el papel sale igual, sin imagen."""
    ruta = Path(settings.LOGO_EMPRESA)
    if not ruta.exists():
        logger.warning("No se encontro el logo de la empresa en %s", ruta)
        return
    try:
        buffer, ancho, alto = _logo_escalado(ruta)
    except Exception:
        logger.exception("No se pudo preparar el logo %s", ruta)
        return

    imagen = ImagenExcel(buffer)
    imagen.width, imagen.height = ancho, alto
    imagen.anchor = OneCellAnchor(
        _from=AnchorMarker(col=0, colOff=pixels_to_EMU(6), row=0, rowOff=pixels_to_EMU(8)),
        ext=XDRPositiveSize2D(cx=pixels_to_EMU(ancho), cy=pixels_to_EMU(alto)),
    )
    hoja.add_image(imagen)


def _escribir_membrete(hoja: Worksheet, titulo: str, empresa: str, generado: date) -> None:
    _insertar_logo(hoja)
    hoja.cell(row=FILA_TITULO, column=3, value=titulo).font = FUENTE_TITULO
    hoja.cell(row=5, column=1, value="Empresa:").font = FUENTE_NEGRITA
    hoja.cell(row=5, column=2, value=empresa or "")
    hoja.cell(row=6, column=1, value="Generado:").font = FUENTE_NEGRITA
    celda = hoja.cell(row=6, column=2, value=generado)
    celda.number_format = FORMATO_FECHA
    celda.alignment = Alignment(horizontal="left")


def _ajustar_anchos(hoja: Worksheet, anchos: list[float]) -> None:
    for i, ancho in enumerate(anchos, start=1):
        hoja.column_dimensions[get_column_letter(i)].width = ancho


# ----------------------------------------------------------------------
# Hojas de detalle
# ----------------------------------------------------------------------

def _armar_tabla(
    hoja: Worksheet,
    columnas: list[str],
    anchos: list[float],
    filas: list[list],
    columna_importe: int,
    relleno: PatternFill | None = None,
) -> None:
    """Encabezado con filtro, filas y total con formula SUM bajo el importe."""
    _ajustar_anchos(hoja, anchos)
    _escribir_encabezados(hoja, FILA_ENCABEZADO, columnas)
    hoja.freeze_panes = hoja.cell(row=FILA_PRIMER_DATO, column=1)

    for k, valores in enumerate(filas):
        _escribir_fila(hoja, FILA_PRIMER_DATO + k, valores, {columna_importe}, relleno=relleno)

    ultima = FILA_PRIMER_DATO + len(filas) - 1
    hoja.auto_filter.ref = (
        f"A{FILA_ENCABEZADO}:{get_column_letter(len(columnas))}{max(ultima, FILA_ENCABEZADO)}"
    )

    fila_total = ultima + 1 if filas else FILA_PRIMER_DATO
    letra = get_column_letter(columna_importe + 1)
    hoja.cell(row=fila_total, column=1, value="TOTAL").font = FUENTE_NEGRITA
    total = hoja.cell(
        row=fila_total,
        column=columna_importe + 1,
        value=f"=SUM({letra}{FILA_PRIMER_DATO}:{letra}{ultima})" if filas else 0,
    )
    total.number_format = FORMATO_IMPORTE
    total.font = FUENTE_NEGRITA
    total.border = BORDE_DOBLE


def _armar_conciliados(hoja: Worksheet, filas: list[dict]) -> None:
    _armar_tabla(
        hoja,
        ["Fecha ARCA", "CUIT agente", "Razón social", "Impuesto", "Certificado",
         "Importe", "Fecha mayor", "Días", "Documento", "Dif. importe", "Criterio", "Estado"],
        [12, 16, 34, 16, 18, 16, 12, 8, 22, 14, 24, 16],
        [
            [_fecha(f["fecha"]), f["cuit"], f["razon_social"], f["impuesto"], f.get("certificado"),
             _importe(f["importe"]), _fecha(f.get("fecha_mayor")), f.get("diferencia_dias"),
             f.get("documento_mayor"), _importe(f.get("diferencia_importe")),
             ETIQUETAS_CRITERIO.get(f.get("criterio"), f.get("criterio")), ESTADO_CONCILIADO]
            for f in filas
        ],
        columna_importe=5,
        relleno=RELLENO_CONCILIADO,
    )


def _armar_faltan_en_arca(hoja: Worksheet, filas: list[dict]) -> None:
    _armar_tabla(
        hoja,
        ["Fecha", "CUIT", "Organización", "Impuesto", "Cuenta", "Documento",
         "Descripción", "Importe", "Estado", "Nota"],
        [12, 16, 34, 16, 32, 22, 40, 16, 18, 60],
        [
            [_fecha(f["fecha"]), f["cuit"], f["razon_social"], f["impuesto"], f.get("cuenta"),
             f.get("documento"), f.get("descripcion"), _importe(f["importe"]), ESTADO_SIN_COINCIDENCIA,
             f.get("nota")]
            for f in filas
        ],
        columna_importe=7,
    )


def _armar_faltan_en_mayor(hoja: Worksheet, filas: list[dict]) -> None:
    _armar_tabla(
        hoja,
        ["Fecha", "CUIT agente", "Razón social", "Impuesto", "Tipo", "Régimen", "Certificado",
         "Comprobante", "Importe", "Estado", "Nota"],
        [12, 16, 34, 16, 12, 28, 18, 20, 16, 18, 60],
        [
            [_fecha(f["fecha"]), f["cuit"], f["razon_social"], f["impuesto"],
             {"Retencion": "Retención", "Percepcion": "Percepción"}.get(f.get("tipo") or "", ""),
             f.get("regimen"), f.get("certificado"), f.get("comprobante"), _importe(f["importe"]),
             ESTADO_SIN_COINCIDENCIA, f.get("nota")]
            for f in filas
        ],
        columna_importe=8,
    )


# ----------------------------------------------------------------------
# Resumen
# ----------------------------------------------------------------------

def _armar_resumen(hoja: Worksheet, resultado: dict) -> None:
    _ajustar_anchos(hoja, [22, 14, 20, 20, 20])
    detalle = resultado["detalle"]

    # Si los dos archivos no son del mismo mes o impuesto, que se vea antes que los numeros.
    for k, aviso in enumerate(resultado.get("avisos") or []):
        celda = hoja.cell(row=FILA_TITULO + 1 + k, column=3, value=f"⚠ {aviso}")
        celda.font = Font(name="Calibri", size=10, bold=True, color="FF9C5700")
    _escribir_encabezados(hoja, FILA_ENCABEZADO, ["Categoría", "Cantidad", "Importe"])
    bandejas = [
        ("A · Conciliados", "conciliados", "Conciliados"),
        ("B · Faltan en ARCA", "faltan_en_arca", "Faltan_en_ARCA"),
        ("C · Faltan en el Mayor", "faltan_en_mayor", "Faltan_en_Mayor"),
    ]
    for k, (etiqueta, clave, hoja_ref) in enumerate(bandejas):
        fila = FILA_PRIMER_DATO + k
        filas_bandeja = detalle[clave]
        _escribir_fila(
            hoja, fila,
            [etiqueta, len(filas_bandeja), sum(_importe(f["importe"]) for f in filas_bandeja)],
            {2},
            relleno=RELLENO_CONCILIADO if clave == "conciliados" else None,
        )
        hoja.cell(row=fila, column=1).hyperlink = f"#{hoja_ref}!A1"

    # Control contra la DDJJ: lo que informa ARCA, lo registrado y la diferencia.
    resumen = resultado.get("resumen", {})
    fila_control = FILA_PRIMER_DATO + len(bandejas) + 1
    for k, (etiqueta, clave) in enumerate(
        [("Informado en ARCA", "importe_arca"), ("Registrado en el Libro Mayor de Xubio", "importe_mayor"), ("Diferencia", "diferencia")]
    ):
        _escribir_fila(
            hoja, fila_control + k, [etiqueta, None, _importe(resumen.get(clave))], {2},
            fuente=FUENTE_NEGRITA if clave == "diferencia" else None,
        )

    # Por impuesto: donde se concentran las diferencias.
    fila_imp = fila_control + 5
    hoja.cell(row=fila_imp - 1, column=1, value="Por impuesto").font = FUENTE_NEGRITA
    _escribir_encabezados(
        hoja, fila_imp,
        ["Impuesto", "Cantidad", "ARCA", "Libro Mayor", "Diferencia", "Conciliado", "Faltan en ARCA", "Faltan en el Mayor"],
    )
    _ajustar_anchos(hoja, [22, 14, 18, 18, 18, 18, 18, 18])
    for k, f in enumerate(resultado.get("por_impuesto") or _por_impuesto_desde_detalle(detalle)):
        _escribir_fila(
            hoja, fila_imp + 1 + k,
            [f["impuesto"], f["cantidad_arca"], f["importe_arca"], f["importe_mayor"], f["diferencia"],
             f["conciliado"], f["faltan_en_arca"], f["faltan_en_mayor"]],
            {2, 3, 4, 5, 6, 7},
        )
    fila = fila_imp + 2 + len(resultado.get("por_impuesto") or _por_impuesto_desde_detalle(detalle))

    # Los primeros casos de B y C: lo que hay que reclamar o ajustar.
    for titulo, filas, columnas, valores in (
        (f"B · Faltan en ARCA (primeros {CASOS_EN_RESUMEN})", detalle["faltan_en_arca"],
         ["Fecha", "CUIT", "Organización", "Impuesto", "Documento", "Importe"],
         lambda f: [_fecha(f["fecha"]), f["cuit"], f["razon_social"], f["impuesto"], f.get("documento"),
                    _importe(f["importe"])]),
        (f"C · Faltan en el Mayor (primeros {CASOS_EN_RESUMEN})", detalle["faltan_en_mayor"],
         ["Fecha", "CUIT agente", "Razón social", "Impuesto", "Certificado", "Importe"],
         lambda f: [_fecha(f["fecha"]), f["cuit"], f["razon_social"], f["impuesto"], f.get("certificado"),
                    _importe(f["importe"])]),
    ):
        fila += 1
        hoja.cell(row=fila, column=1, value=titulo).font = FUENTE_NEGRITA
        _escribir_encabezados(hoja, fila + 1, columnas)
        fila += 2
        if not filas:
            hoja.cell(row=fila, column=1, value="Sin casos.")
            fila += 1
        for f in filas[:CASOS_EN_RESUMEN]:
            _escribir_fila(hoja, fila, valores(f), {5})
            fila += 1


def _por_impuesto_desde_detalle(detalle: dict) -> list[dict]:
    """Para resultados sin `por_impuesto` (los arma el conciliador): mismos totales desde el detalle."""
    filas: dict[str, dict] = defaultdict(
        lambda: {"cantidad_arca": 0, "importe_arca": 0.0, "importe_mayor": 0.0,
                 "conciliado": 0.0, "faltan_en_arca": 0.0, "faltan_en_mayor": 0.0}
    )
    for f in detalle["conciliados"]:
        d = filas[f.get("impuesto") or "Sin identificar"]
        d["cantidad_arca"] += 1
        d["importe_arca"] += _importe(f["importe"])
        d["importe_mayor"] += _importe(f.get("importe_mayor", f["importe"]))
        d["conciliado"] += _importe(f["importe"])
    for f in detalle["faltan_en_arca"]:
        d = filas[f.get("impuesto") or "Sin identificar"]
        d["importe_mayor"] += _importe(f["importe"])
        d["faltan_en_arca"] += _importe(f["importe"])
    for f in detalle["faltan_en_mayor"]:
        d = filas[f.get("impuesto") or "Sin identificar"]
        d["cantidad_arca"] += 1
        d["importe_arca"] += _importe(f["importe"])
        d["faltan_en_mayor"] += _importe(f["importe"])
    return [
        {"impuesto": k, **v, "diferencia": round(v["importe_arca"] - v["importe_mayor"], 2)}
        for k, v in sorted(filas.items())
    ]


def _armar_para_revisar(hoja: Worksheet, sugerencias: list[dict]) -> None:
    """Pares que el cruce no hizo solo pero que probablemente sean la misma retención."""
    _armar_tabla(
        hoja,
        ["Fecha ARCA", "CUIT agente", "Razón social", "Impuesto ARCA", "Certificado", "Importe ARCA",
         "Fecha mayor", "Documento", "Importe mayor", "Motivo"],
        [12, 16, 34, 16, 18, 16, 12, 22, 16, 60],
        [
            [_fecha(s["arca"]["fecha"]), s["arca"]["cuit"], s["arca"]["razon_social"], s["arca"]["impuesto"],
             s["arca"].get("certificado"), _importe(s["arca"]["importe"]), _fecha(s["mayor"]["fecha"]),
             s["mayor"].get("documento"), _importe(s["mayor"]["importe"]), s["motivo"]]
            for s in sugerencias
        ],
        columna_importe=5,
    )


# ----------------------------------------------------------------------
# Entrada
# ----------------------------------------------------------------------

def exportar(resultado: dict, empresa: str | None = None, hoy: date | None = None) -> bytes:
    """El .xlsx con el resultado del cruce (el mismo dict que va al front)."""
    dia = hoy or date.today()
    empresa = empresa or settings.EMPRESA
    detalle = resultado["detalle"]

    libro = Workbook()
    hojas = [
        ("Resumen", "Conciliación de Retenciones ARCA", lambda h: _armar_resumen(h, resultado)),
        ("Conciliados", "A · Retenciones conciliadas", lambda h: _armar_conciliados(h, detalle["conciliados"])),
        ("Faltan_en_ARCA", "B · Registradas en el Libro Mayor de Xubio que no figuran en ARCA",
         lambda h: _armar_faltan_en_arca(h, detalle["faltan_en_arca"])),
        ("Faltan_en_Mayor", "C · Declaradas en ARCA sin registrar en el Libro Mayor de Xubio",
         lambda h: _armar_faltan_en_mayor(h, detalle["faltan_en_mayor"])),
    ]
    if resultado.get("sugerencias"):
        hojas.append(
            ("Para revisar", "Posibles pares para confirmar a mano",
             lambda h: _armar_para_revisar(h, resultado["sugerencias"]))
        )
    for k, (nombre, titulo, armar) in enumerate(hojas):
        hoja = libro.active if k == 0 else libro.create_sheet()
        hoja.title = nombre
        _escribir_membrete(hoja, titulo, empresa, dia)
        armar(hoja)

    destino = io.BytesIO()
    libro.save(destino)
    return destino.getvalue()


def nombre_archivo(empresa: str | None = None, hoy: date | None = None) -> str:
    """'Retenciones ARCA PICCININI Y ASOCIADOS S.A. 02-11-2026.xlsx'"""
    dia = hoy or date.today()
    empresa = (empresa or settings.EMPRESA or "").strip()
    limpio = " ".join("".join(c if c.isalnum() or c in " -_.&" else "" for c in empresa).split())
    partes = ["Retenciones ARCA", limpio, dia.strftime("%d-%m-%Y")]
    return " ".join(p for p in partes if p) + ".xlsx"
