"""
Arma el Excel de la conciliacion de retenciones, en el formato en que se
trabaja a mano: las dos planillas originales, una en cada hoja, con lo
conciliado pintado de amarillo.

Hojas:
    ARCA         El export de Mis Retenciones tal cual (mismas columnas y
                 orden), el importe conciliado en amarillo y el total al pie.
    LIBRO MAYOR  El Libro Mayor de Xubio tal cual, con el Debe conciliado en
                 amarillo y el saldo corrido con formula. Debajo, sin encabezado
                 de columnas, cada uno con su titulo y separados por dos filas
                 en blanco:
                   - «Partidas de ARCA pendientes de registracion»: lo que
                     informa ARCA y no esta en el mayor (tipo, agente e importe
                     en el Debe), y
                   - «Partidas contabilizadas - revisar procedencia / revisar
                     cliente»: lo que esta en el mayor y ARCA no informa,
                     repetido con el importe en el Haber,
                 asi el saldo final da lo que informa ARCA. Al pie va el control:
                 saldo ARCA, saldo Xubio (el saldo corrido ajustado) y la
                 diferencia, que deberia dar 0.
"""

from __future__ import annotations

import io
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.core.config import settings
from app.services.conciliador import id_arca, id_mayor
from app.services.importador_tablas import _traducir_columna
from app.services.tools import limpiar_numero, parse_fecha

# Los formatos de cada export: el contable de ARCA y el de Xubio.
FORMATO_IMPORTE_ARCA = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
FORMATO_IMPORTE_MAYOR = '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'
FORMATO_FECHA_ARCA = "dd/mm/yyyy"
FORMATO_FECHA_MAYOR = "mm-dd-yy"  # el formato corto de fecha de Excel, como lo exporta Xubio

RELLENO_CONCILIADO = PatternFill("solid", fgColor="FFFFFF00")  # amarillo: tiene coincidencia

# Filas en blanco entre el mayor y cada apartado de abajo (y antes del total de ARCA).
FILAS_EN_BLANCO = 2

# Los apartados debajo del mayor.
TITULO_PENDIENTES = "PARTIDAS DE ARCA PENDIENTES DE REGISTRACIÓN"  # ARCA las informa, el mayor no
TITULO_CONTABILIZADAS = "PARTIDAS CONTABILIZADAS - REVISAR PROCEDENCIA / REVISAR CLIENTE"  # al reves
NEGRITA = Font(bold=True)
# Si el control no da 0: la DIF y los motivos al pie, en rojo.
ROJO = "FFC00000"
TITULO_MOTIVOS = "MOTIVOS DE LA DIFERENCIA:"
CENTAVO = 0.005

# Si la fila no trae lo que vino en el archivo (otra fuente), se arma con estas columnas.
COLUMNAS_ARCA = [
    "CUIT Agente Ret./Perc.", "Denominación o Razón Social", "Impuesto", "Descripción Impuesto",
    "Régimen", "Descripción Régimen", "Fecha Ret./Perc.", "Número Certificado",
    "Descripción Operación", "Importe Ret./Perc.", "Número Comprobante", "Fecha Comprobante",
    "Descripción Comprobante", "Fecha Registración DJ Ag.Ret.",
]
COLUMNAS_MAYOR = ["Fecha", "Documento", "Cuenta", "Debe", "Haber", "Saldo", "Descripción", "Organización"]

# Anchos de las columnas de los exports de ejemplo (por posicion).
ANCHOS_ARCA = [20.6, 40, 8.6, 32.3, 8.3, 60, 15.1, 16.9, 19.9, 16.3, 19.1, 17.9, 22.6, 27]
ANCHOS_MAYOR = [10.1, 26.6, 36.4, 12.9, 12.9, 13.1, 40, 31.1, 15.4]


# Utilidades

def _columnas_por_rol(encabezados: list[str]) -> dict[str, int]:
    """{FECHA: 0, DEBE: 3, ...}: solo encabezados que coinciden exacto con un alias."""
    roles: dict[str, int] = {}
    for k, nombre in enumerate(encabezados):
        canonica, exacta = _traducir_columna(nombre)
        if canonica and exacta:
            roles.setdefault(canonica, k)
    return roles


def _encabezados(filas: list[dict], por_defecto: list[str]) -> list[str]:
    """Las columnas del archivo, sin las que no tienen titulo (anotaciones a mano al costado)."""
    originales = next((list(f["original"]) for f in filas if f.get("original")), por_defecto)
    return [c for c in originales if not str(c).startswith("Unnamed:")]


def _numero(valor) -> float | None:
    n = round(limpiar_numero(valor), 2)
    return n or None


def _valor_celda(valor):
    if isinstance(valor, datetime):
        return valor.date()
    return valor


def _escribir(hoja: Worksheet, fila: int, valores: list, formato_fecha: str = FORMATO_FECHA_ARCA) -> None:
    for k, valor in enumerate(valores, start=1):
        if valor is not None and valor != "":
            if isinstance(valor, str):
                # Caracteres de control que openpyxl no acepta (vienen en algun export).
                valor = ILLEGAL_CHARACTERS_RE.sub("", valor)
            celda = hoja.cell(row=fila, column=k, value=_valor_celda(valor))
            if isinstance(valor, str) and valor.startswith("="):
                celda.data_type = "s"  # un texto que empieza con "=" no es una formula
            if isinstance(celda.value, date):
                celda.number_format = formato_fecha


def _ajustar_anchos(hoja: Worksheet, anchos: list[float], cantidad: int) -> None:
    for k in range(cantidad):
        hoja.column_dimensions[get_column_letter(k + 1)].width = anchos[k] if k < len(anchos) else 15


# Lo que ocupa una fecha (dd/mm/yyyy) o un importe con el formato contable, en caracteres.
ANCHO_FECHA = 12
MARGEN_IMPORTE = 4  # los espacios y el signo del formato contable


def _ensanchar_para_numeros(hoja: Worksheet) -> None:
    """
    Agranda las columnas donde una fecha o un importe no entra: Excel los
    muestra como #### si la columna es mas angosta (el texto, en cambio, se
    corta o desborda, asi que no se toca).
    """
    necesarios: dict[str, float] = {}
    for fila in hoja.iter_rows():
        for celda in fila:
            valor = celda.value
            if isinstance(valor, (date, datetime)):
                largo = ANCHO_FECHA
            elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
                largo = len(f"{valor:,.2f}") + MARGEN_IMPORTE
            elif isinstance(valor, str) and valor.startswith("="):
                largo = 16  # saldo o total con formula: el mayor importe razonable
            else:
                continue
            letra = celda.column_letter
            necesarios[letra] = max(necesarios.get(letra, 0), largo)
    for letra, largo in necesarios.items():
        dimension = hoja.column_dimensions[letra]
        if (dimension.width or 0) < largo:
            dimension.width = largo


# Hoja ARCA

def _original_arca(r: dict) -> dict:
    """La fila tal cual vino; si no se tiene, armada con los datos leidos."""
    if r.get("original"):
        return r["original"]
    tipo = {"Retencion": "RETENCION", "Percepcion": "PERCEPCION"}.get(r.get("tipo") or "", "")
    return dict(zip(COLUMNAS_ARCA, [
        r.get("cuit"), r.get("razon_social"), None, r.get("impuesto"), None, r.get("regimen"),
        r.get("fecha"), r.get("certificado"), tipo, r.get("importe"), r.get("comprobante"), None, None, None,
    ]))


def _armar_arca(hoja: Worksheet, arca: list[dict], conciliados: set[str]) -> str | None:
    """Arma la hoja y devuelve la celda del total ("J55"), o None si no hay importes."""
    encabezados = _encabezados(arca, COLUMNAS_ARCA)
    roles = _columnas_por_rol(encabezados)
    col_importe = roles.get("IMPORTE")
    col_fecha = roles.get("FECHA")

    _ajustar_anchos(hoja, ANCHOS_ARCA, len(encabezados))
    _escribir(hoja, 1, encabezados)

    for i, r in enumerate(arca):
        fila = 2 + i
        original = _original_arca(r)
        valores = [original.get(c) for c in encabezados]
        if col_fecha is not None:
            valores[col_fecha] = parse_fecha(r.get("fecha")) or valores[col_fecha]
        if col_importe is not None:
            valores[col_importe] = r["importe"]
        _escribir(hoja, fila, valores)

        if col_importe is not None:
            celda = hoja.cell(row=fila, column=col_importe + 1)
            celda.number_format = FORMATO_IMPORTE_ARCA
            if id_arca(i) in conciliados:
                celda.fill = RELLENO_CONCILIADO

    if col_importe is not None and arca:
        letra = get_column_letter(col_importe + 1)
        ultima = 1 + len(arca)
        total = hoja.cell(row=ultima + FILAS_EN_BLANCO + 1, column=col_importe + 1, value=f"=SUM({letra}2:{letra}{ultima})")
        total.number_format = FORMATO_IMPORTE_ARCA
        return total.coordinate
    return None


# Hoja LIBRO MAYOR

def _original_mayor(m: dict) -> dict:
    if m.get("original"):
        return m["original"]
    return dict(zip(COLUMNAS_MAYOR, [
        m.get("fecha"), m.get("documento"), m.get("cuenta"), m.get("importe"), None, None,
        m.get("descripcion"), m.get("razon_social"),
    ]))


class _HojaMayor:
    """Escribe filas del mayor llevando el saldo corrido con formula."""

    def __init__(self, hoja: Worksheet, encabezados: list[str]):
        self.hoja = hoja
        self.encabezados = encabezados
        roles = _columnas_por_rol(encabezados)
        self.fecha, self.debe, self.haber, self.saldo = (roles.get(r) for r in ("FECHA", "DEBE", "HABER", "SALDO"))
        self.fila = 1
        self.fila_saldo: int | None = None  # la ultima fila con saldo, para encadenar la formula

    def encabezado(self) -> None:
        _escribir(self.hoja, self.fila, self.encabezados)
        for col in (self.debe, self.haber, self.saldo):
            if col is not None:
                self.hoja.cell(row=self.fila, column=col + 1).number_format = FORMATO_IMPORTE_MAYOR
        self.fila += 1

    def en_blanco(self, cantidad: int) -> None:
        self.fila += cantidad

    def titulo(self, texto: str) -> None:
        """El nombre de un apartado, en negrita en la primera columna."""
        self.hoja.cell(row=self.fila, column=1, value=texto).font = NEGRITA
        self.fila += 1

    def control(self, total_arca: str | None) -> int:
        """
        Saldo ARCA (el total de la hoja ARCA), saldo Xubio (el saldo corrido
        despues de los apartados) y la diferencia, todo con formulas.
        """
        h = self.hoja
        if self.saldo is not None:
            columna = self.saldo + 1
            saldo_xubio = f"={get_column_letter(columna)}{self.fila_saldo}" if self.fila_saldo else "=0"
        else:
            # Sin columna de saldo: Debe menos Haber de todo lo de arriba.
            columna = self.haber + 1
            d, e, ultima = get_column_letter(self.debe + 1), get_column_letter(self.haber + 1), self.fila - 1
            saldo_xubio = f"=SUM({d}2:{d}{ultima})-SUM({e}2:{e}{ultima})"
        etiqueta = max(columna - 1, 1)
        valor = get_column_letter(columna)
        primera = self.fila
        filas = [
            ("SALDO ARCA:", f"='ARCA'!{total_arca}" if total_arca else "=0"),
            ("SALDO XUBIO:", saldo_xubio),
            ("DIF:", f"={valor}{primera}-{valor}{primera + 1}"),
        ]
        for nombre, formula in filas:
            h.cell(row=self.fila, column=etiqueta, value=nombre).font = NEGRITA
            celda = h.cell(row=self.fila, column=columna, value=formula)
            celda.number_format = FORMATO_IMPORTE_MAYOR
            celda.font = NEGRITA
            self.fila += 1
        return columna

    def motivos(self, columna: int, motivos: list[tuple[str, float]]) -> None:
        """Debajo del control, por que la diferencia no da 0: cada motivo con lo que suma."""
        h = self.hoja
        h.cell(row=self.fila - 1, column=columna).font = Font(bold=True, color=ROJO)  # el DIF
        self.fila += 1
        h.cell(row=self.fila, column=1, value=TITULO_MOTIVOS).font = Font(bold=True, color=ROJO)
        self.fila += 1
        for texto, importe in motivos:
            h.cell(row=self.fila, column=1, value=texto).font = Font(color=ROJO)
            celda = h.cell(row=self.fila, column=columna, value=round(importe, 2))
            celda.number_format = FORMATO_IMPORTE_MAYOR
            celda.font = Font(color=ROJO)
            self.fila += 1

    def escribir(self, valores: list, debe: float | None, haber: float | None, saldo_original=None,
                 conciliado: bool = False) -> tuple[int, float]:
        """Escribe la fila; devuelve (numero de fila, cuanto suma al saldo Xubio)."""
        h, f = self.hoja, self.fila
        aporte = (debe or 0) - (haber or 0)
        if self.saldo is not None and self.fila_saldo is None and saldo_original is not None:
            aporte = saldo_original  # la primera fila arranca con el saldo que trae
        valores = list(valores)
        if self.debe is not None:
            valores[self.debe] = debe
        if self.haber is not None:
            valores[self.haber] = haber
        if self.saldo is not None:
            valores[self.saldo] = None
        _escribir(h, f, valores, FORMATO_FECHA_MAYOR)

        for col in (self.debe, self.haber):
            if col is not None:
                h.cell(row=f, column=col + 1).number_format = FORMATO_IMPORTE_MAYOR
        if conciliado and self.debe is not None:
            h.cell(row=f, column=self.debe + 1).fill = RELLENO_CONCILIADO

        if self.saldo is not None and self.debe is not None and self.haber is not None:
            d, e = get_column_letter(self.debe + 1), get_column_letter(self.haber + 1)
            s = get_column_letter(self.saldo + 1)
            if self.fila_saldo is not None:
                saldo = f"=+{s}{self.fila_saldo}+{d}{f}-{e}{f}"
            elif saldo_original is not None:
                saldo = saldo_original  # el saldo con que arranca el mayor
            else:
                saldo = f"=+{d}{f}-{e}{f}"
            celda = h.cell(row=f, column=self.saldo + 1, value=saldo)
            celda.number_format = FORMATO_IMPORTE_MAYOR
            self.fila_saldo = f
        self.fila += 1
        return f, aporte


def _armar_mayor(
    hoja: Worksheet,
    mayor,
    arca: list[dict],
    detalle: dict,
    hasta_periodo: date | None = None,
    total_arca: str | None = None,
) -> None:
    """Arma la hoja. Si el control no da 0, escribe debajo los motivos (con la DIF en rojo)."""
    conciliados = {c["id_mayor"] for c in detalle["conciliados"]}
    redondeos = {c["id_mayor"]: c for c in detalle["conciliados"] if abs(c.get("diferencia_importe") or 0) >= CENTAVO}
    retenciones = [{**m, "_id": id_mayor(j)} for j, m in enumerate(mayor.retenciones)]

    def del_periodo(m: dict) -> bool:
        # El bot pide el mayor unos dias de mas para atrapar los cobros cargados
        # tarde; lo de esos dias que no se concilio es del mes siguiente.
        fecha = m.get("fecha")
        return hasta_periodo is None or fecha is None or fecha <= hasta_periodo or m.get("_id") in conciliados

    filas = sorted(
        [m for m in retenciones if del_periodo(m)]
        + [{**m, "_tipo": "aplicacion"} for m in mayor.aplicaciones if del_periodo(m)]
        + [{**m, "_tipo": "inicial"} for m in mayor.saldos_iniciales],
        key=lambda m: m.get("orden", 0),
    )
    encabezados = _encabezados(filas, COLUMNAS_MAYOR)
    escritor = _HojaMayor(hoja, encabezados)
    if escritor.debe is None or escritor.haber is None:
        # Un mayor con una sola columna de importe: se arma con las columnas de Xubio.
        filas = [{**m, "original": None} for m in filas]
        encabezados = COLUMNAS_MAYOR
        escritor = _HojaMayor(hoja, encabezados)

    _ajustar_anchos(hoja, ANCHOS_MAYOR, len(encabezados))
    escritor.encabezado()

    def valores_de(m: dict) -> tuple[list, dict]:
        original = _original_mayor(m)
        valores = [original.get(c) for c in encabezados]
        if escritor.fecha is not None:
            valores[escritor.fecha] = parse_fecha(valores[escritor.fecha]) or valores[escritor.fecha]
        return valores, original

    aportes: list[float] = []
    responsables: dict[str, list[float]] = {"redondeos": [], "iniciales": [], "aplicaciones": []}
    for m in filas:
        valores, original = valores_de(m)
        if m.get("original"):
            debe = _numero(original.get(encabezados[escritor.debe]))
            haber = _numero(original.get(encabezados[escritor.haber]))
        else:
            # Sin la fila original: las retenciones van al Debe y las aplicaciones al Haber.
            debe, haber = (m.get("importe"), None) if m.get("_id") else (None, m.get("importe"))
        saldo = _numero(original.get(encabezados[escritor.saldo])) if escritor.saldo is not None else None
        _, aporte = escritor.escribir(valores, debe, haber, saldo_original=saldo, conciliado=m.get("_id") in conciliados)
        aportes.append(aporte)
        # Lo que el saldo Xubio tiene y ARCA nunca informa: el saldo con que arranca
        # el mayor (el inicial, la apertura) y las aplicaciones del Haber.
        tipo = m.get("_tipo")
        de_arrastre = aporte if tipo else aporte - ((debe or 0) - (haber or 0))
        if tipo == "aplicacion" and abs(aporte) >= CENTAVO:
            responsables["aplicaciones"].append(aporte)
        elif abs(de_arrastre) >= CENTAVO:
            responsables["iniciales"].append(de_arrastre)
        if m.get("_id") in redondeos:
            responsables["redondeos"].append(redondeos[m["_id"]]["diferencia_importe"])

    # Lo que informa ARCA y no esta en el mayor: tipo, agente e importe en el Debe.
    faltan_en_mayor = detalle["faltan_en_mayor"]
    if faltan_en_mayor:
        escritor.en_blanco(FILAS_EN_BLANCO)
        escritor.titulo(TITULO_PENDIENTES)
        for f in faltan_en_mayor:
            r = arca[int(f["id"][1:])]  # id_arca(i)
            valores = [None] * len(encabezados)
            valores[0] = _tipo_arca(r)
            if len(valores) > 1:
                valores[1] = r.get("razon_social")
            aportes.append(escritor.escribir(valores, f["importe"], 0)[1])

    # Lo que esta en el mayor y ARCA no informa: el mismo asiento, con el importe en el Haber.
    faltan_en_arca = detalle["faltan_en_arca"]
    if faltan_en_arca:
        escritor.en_blanco(FILAS_EN_BLANCO)
        escritor.titulo(TITULO_CONTABILIZADAS)
        por_id = {m["_id"]: m for m in filas if m.get("_id")}
        for f in faltan_en_arca:
            m = por_id.get(f["id"], f)
            valores, _ = valores_de(m)
            aportes.append(escritor.escribir(valores, None, f["importe"])[1])

    # El control: con los dos apartados, el mayor tiene que dar lo que informa ARCA.
    escritor.en_blanco(FILAS_EN_BLANCO)
    columna = escritor.control(total_arca)

    importe_arca = sum(r["importe"] for r in arca) if total_arca else 0
    diferencia = round(importe_arca - sum(aportes), 2)
    if abs(diferencia) < CENTAVO:
        return

    # Por que no da 0. Cada motivo suma (o resta) a la diferencia ARCA − Xubio.
    def total(clave: str) -> float:
        return sum(responsables[clave])

    if abs(total("iniciales") + total("aplicaciones")) < CENTAVO:
        # La apertura y la aplicacion del Haber se compensan: no son responsables.
        responsables["iniciales"], responsables["aplicaciones"] = [], []

    motivos = [
        (f"Conciliadas con importe distinto entre ARCA y el asiento: {len(responsables['redondeos'])} (redondeos al cargar)",
         total("redondeos")),
        ("Saldo inicial / asiento de apertura del mayor: ARCA no lo informa", -total("iniciales")),
        (f"Aplicaciones del Haber (DDJJ, anulaciones): {len(responsables['aplicaciones'])}, ARCA no las informa",
         -total("aplicaciones")),
    ]
    motivos = [(texto, importe) for texto, importe in motivos if abs(importe) >= CENTAVO]
    otras = diferencia - sum(importe for _, importe in motivos)
    if abs(otras) >= CENTAVO:
        motivos.append(("Otras diferencias (filas cambiadas a mano o con importes que no se pudieron leer)", otras))
    escritor.motivos(columna, motivos)


def _tipo_arca(r: dict) -> str:
    """'RETENCION' o 'PERCEPCION', como lo escribe ARCA en «Descripción Operación»."""
    original = r.get("original") or {}
    for nombre, valor in original.items():
        if _traducir_columna(nombre)[0] == "DETALLE" and valor:
            return str(valor).strip()
    return {"Retencion": "RETENCION", "Percepcion": "PERCEPCION"}.get(r.get("tipo") or "", "RETENCION")


# Entrada

def exportar(resultado: dict, entrada) -> bytes:
    """
    El .xlsx con el resultado del cruce. `entrada` es lo leido de cada lado
    (tareas.Entrada): las filas originales de ARCA y del mayor, en el orden
    del archivo, para copiar las dos planillas iguales.
    """
    detalle = resultado["detalle"]
    conciliados_arca = {c["id"] for c in detalle["conciliados"]}

    libro = Workbook()
    hoja_arca = libro.active
    hoja_arca.title = "ARCA"
    total_arca = _armar_arca(hoja_arca, entrada.arca, conciliados_arca)

    hoja_mayor = libro.create_sheet("LIBRO MAYOR")
    _armar_mayor(hoja_mayor, entrada.mayor, entrada.arca, detalle, getattr(entrada, "hasta_periodo", None), total_arca)
    for hoja in (hoja_arca, hoja_mayor):
        _ensanchar_para_numeros(hoja)

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
