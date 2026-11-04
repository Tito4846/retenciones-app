"""
Lectura de tablas (xlsx/xls/csv) con encabezados que no controlamos.

Es el mismo lector del modulo de conciliacion bancaria: los exports de ARCA
("Mis Retenciones") y de Xubio (Libro Mayor) llegan con titulos arriba del
encabezado, en cp1252 o utf-8, separados por ';' o ','. Lo unico que cambia son
los alias de columnas: aca se suman los de ARCA (CUIT del agente, numero de
certificado, impuesto) a los del mayor contable.
"""

import csv
import io
import logging
import os
import unicodedata
from collections.abc import Callable

import pandas as pd

from app.services.tools import ErrorDeLectura

logger = logging.getLogger(__name__)

EXTENSIONES = {".xlsx", ".xls", ".csv", ".txt"}

# De cada columna canonica, los encabezados que se ven en los exports. Se
# comparan ya normalizados (mayusculas, sin acentos ni signos).
ALIAS_COLUMNAS = {
    "FECHA": {
        "FECHA",
        "FECHARETPERC",
        "FECHARETENCION",
        "FECHAPERCEPCION",
        "FECHARETENCIONPERCEPCION",
        "FECHADERETENCION",
        "FECHAOPERACION",
        "FECHACONTABLE",
        "FECHAASIENTO",
    },
    "DETALLE": {
        "DETALLE",
        "CONCEPTO",
        "DESCRIPCION",
        "GLOSA",
        "OBSERVACION",
        "DESCRIPCIONOPERACION",
    },
    "DEBE": {"DEBE", "DEBITO", "DEBITOS"},
    "HABER": {"HABER", "CREDITO", "CREDITOS"},
    # ARCA trae una sola columna con el importe retenido.
    "IMPORTE": {
        "IMPORTE",
        "IMPORTERETPERC",
        "IMPORTERETENIDO",
        "IMPORTEPERCIBIDO",
        "IMPORTERETENCION",
        "IMPORTEPERCEPCION",
        "MONTORETENIDO",
        "MONTO",
    },
    "SALDO": {"SALDO", "SALDOACUMULADO"},
    "CUIT": {
        "CUIT",
        "CUITAGENTE",
        "CUITAGENTERETPERC",
        "CUITAGENTERETENCION",
        "CUITAGENTEDERETENCION",
        "CUITDELAGENTE",
        "CUITRETENEDOR",
        "CUITORGANIZACION",
        "CUITCLIENTE",
    },
    # El agente de retencion en ARCA y la Organizacion del asiento en Xubio
    # son la misma persona vista desde los dos lados.
    "RAZON_SOCIAL": {
        "RAZONSOCIAL",
        "DENOMINACION",
        "DENOMINACIONAGENTE",
        "DENOMINACIONDELAGENTE",
        "AGENTE",
        "AGENTERETENCION",
        "ORGANIZACION",
        "ORGANIZACIONNOMBRE",
        "CLIENTE",
    },
    # El codigo del impuesto ("217", "767") y su descripcion van separados: si
    # cayeran en la misma canonica se perderia la descripcion.
    "IMPUESTO": {"IMPUESTO", "CODIGOIMPUESTO", "TRIBUTO"},
    "DESCRIPCION_IMPUESTO": {"DESCRIPCIONIMPUESTO", "DESCIMPUESTO"},
    "REGIMEN": {"REGIMEN", "DESCRIPCIONREGIMEN", "CODIGOREGIMEN"},
    "CERTIFICADO": {
        "CERTIFICADO",
        "NUMEROCERTIFICADO",
        "NROCERTIFICADO",
        "NUMERODECERTIFICADO",
        "NROCERT",
    },
    "COMPROBANTE": {
        "COMPROBANTE",
        "NUMEROCOMPROBANTE",
        "NROCOMPROBANTE",
    },
    "DOCUMENTO": {"DOCUMENTO", "NUMERODOCUMENTO", "NRODOCUMENTO"},
    "CUENTA": {"CUENTA", "NOMBRECUENTA", "CUENTACONTABLE"},
}

# Cuantas filas se miran para encontrar el encabezado cuando el archivo viene
# con un titulo arriba ("Mis Retenciones - Periodo 10/2026").
FILAS_PARA_BUSCAR_ENCABEZADO = 15

EsEncabezado = Callable[[set], bool]


def _normalizar(texto) -> str:
    """'Fecha Ret./Perc.' -> 'FECHARETPERC'."""
    sin_acentos = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in sin_acentos.upper() if c.isalnum())


def _traducir_columna(nombre) -> tuple[str | None, bool]:
    """
    (canonica, coincide_exacta) para un encabezado, o (None, False).

    Primero coincidencia exacta; si no hay, containment con el alias mas largo
    (minimo 6 letras, para que "DEBE" no se coma un "DETALLE DE DEBITO").
    """
    n = _normalizar(nombre)
    if not n:
        return None, False

    for canonica, alias in ALIAS_COLUMNAS.items():
        if n in alias:
            return canonica, True

    mejor = None
    for canonica, alias in ALIAS_COLUMNAS.items():
        for nombre_alias in alias:
            if len(nombre_alias) >= 6 and nombre_alias in n:
                if mejor is None or len(nombre_alias) > mejor[1]:
                    mejor = (canonica, len(nombre_alias))
    if mejor is None:
        return None, False
    return mejor[0], False


def columna_canonica(nombre) -> str | None:
    return _traducir_columna(nombre)[0]


def mapa_de_columnas(df: pd.DataFrame) -> dict[str, str]:
    """
    {nombre original: canonica} de las columnas que se van a renombrar.

    Las exactas ganan sobre las que solo contienen al alias, y entre iguales
    manda el orden de la hoja. Asi "Fecha Comprobante" (containment) no le gana
    a "Fecha Ret./Perc." (exacta).
    """
    exactas: dict[str, str] = {}
    por_contencion: dict[str, str] = {}

    for nombre in df.columns:
        canonica, exacta = _traducir_columna(nombre)
        if canonica is None:
            continue
        destino = exactas if exacta else por_contencion
        destino.setdefault(canonica, nombre)

    elegidas = dict(por_contencion)
    elegidas.update(exactas)

    return {
        nombre: canonica
        for canonica, nombre in elegidas.items()
        if canonica not in df.columns or nombre == canonica
    }


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renombra los encabezados reconocidos a los nombres canonicos.

    Las columnas que no se reconocen se conservan. Si dos columnas caerian en
    la misma canonica se renombra solo la mejor (ver mapa_de_columnas):
    renombrar las dos dejaria dos FECHA y pandas leeria la primera en silencio.
    """
    renombres = {orig: can for orig, can in mapa_de_columnas(df).items() if orig != can}
    if renombres:
        logger.info("Columnas normalizadas: %s", renombres)
        df = df.rename(columns=renombres)
    return df


def _fila_del_encabezado(df_crudo: pd.DataFrame, es_encabezado: EsEncabezado):
    """
    Numero de fila donde estan los nombres de columna, o None si la primera fila
    ya los tiene.
    """
    primeras = {columna_canonica(c) for c in df_crudo.columns} - {None}
    if es_encabezado(primeras):
        return None

    for indice in range(min(FILAS_PARA_BUSCAR_ENCABEZADO, len(df_crudo))):
        fila = df_crudo.iloc[indice]
        if es_encabezado({columna_canonica(v) for v in fila} - {None}):
            return indice
    return None


def _encabezado_de_filas(filas, es_encabezado: EsEncabezado):
    for indice in range(min(FILAS_PARA_BUSCAR_ENCABEZADO, len(filas))):
        canonicas = {columna_canonica(v) for v in filas[indice]} - {None}
        if es_encabezado(canonicas):
            return indice
    return None


def _leer_csv(ruta: str, es_encabezado: EsEncabezado) -> pd.DataFrame:
    """
    Los CSV argentinos vienen en cp1252/latin-1 y separados por ';', pero
    algunos exports son utf-8 con ','. Se prueba a mano: el sniffer de pandas se
    confunde con titulos arriba y numeros con coma decimal.
    """
    ultimo_error = None
    for codificacion in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with open(ruta, "r", encoding=codificacion, errors="strict") as fh:
                contenido = fh.read()
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
            continue

        for separador in (";", ",", "\t", "|"):
            try:
                filas = list(csv.reader(io.StringIO(contenido), delimiter=separador))
            except Exception as e:  # noqa: BLE001
                ultimo_error = e
                continue
            if len(filas) < 2 or max(len(f) for f in filas) < 2:
                continue

            fila_encabezado = _encabezado_de_filas(filas, es_encabezado)
            if fila_encabezado is None:
                continue

            encabezados = [str(h or "") for h in filas[fila_encabezado]]
            datos = [f for f in filas[fila_encabezado + 1 :] if any(c.strip() for c in f)]
            df = pd.DataFrame(datos)
            if df.empty:
                continue
            df = df.iloc[:, : len(encabezados)]
            df.columns = encabezados[: len(df.columns)]
            logger.info(
                "CSV leido como %s separador %r, encabezado en la fila %d",
                codificacion,
                separador,
                fila_encabezado + 1,
            )
            return df
    raise ErrorDeLectura(
        "No se encontro el encabezado de la tabla en el CSV."
        + (f" Ultimo error: {ultimo_error}" if ultimo_error else "")
    )


def _leer_xlsx(ruta: str, es_encabezado: EsEncabezado) -> pd.DataFrame:
    crudo = pd.read_excel(ruta, header=None, dtype=object)
    fila_encabezado = _fila_del_encabezado(crudo, es_encabezado)
    if fila_encabezado is None:
        return pd.read_excel(ruta, dtype=object)
    logger.info("Encabezado de la tabla en la fila %s", fila_encabezado + 1)
    return pd.read_excel(ruta, header=fila_encabezado, dtype=object)


def valores_originales(columnas: list[str], fila: pd.Series) -> dict:
    """
    {encabezado original: valor} de una fila, tal como vino en el archivo. Lo
    usa el Excel de salida para copiar las hojas de ARCA y del mayor iguales.
    `columnas` son los encabezados antes de normalizar_columnas (que solo
    renombra: el orden de los valores es el mismo).
    """
    salida = {}
    for nombre, valor in zip(columnas, fila.tolist()):
        if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
            valor = None
        elif isinstance(valor, pd.Timestamp):
            valor = valor.to_pydatetime()
        salida[nombre] = valor
    return salida


def leer_tabla(ruta: str, es_encabezado: EsEncabezado) -> pd.DataFrame:
    """Lee un Excel/CSV con el encabezado que decide es_encabezado."""
    extension = os.path.splitext(ruta)[1].lower()
    if extension not in EXTENSIONES:
        raise ErrorDeLectura(
            f"Formato no soportado: '{extension}'. Se aceptan {', '.join(sorted(EXTENSIONES))}."
        )
    if not os.path.isfile(ruta):
        raise ErrorDeLectura(f"No se encuentra el archivo a procesar: {ruta}")

    try:
        if extension in (".xlsx", ".xls"):
            df = _leer_xlsx(ruta, es_encabezado)
        else:
            df = _leer_csv(ruta, es_encabezado)
    except ErrorDeLectura:
        raise
    except Exception as e:
        # openpyxl/xlrd tiran sus propias excepciones cuando el archivo esta
        # corrupto: sin esto el usuario veria un traceback en vez de un motivo.
        logger.exception("No se pudo leer %s", ruta)
        raise ErrorDeLectura(f"No se pudo leer el archivo: {e}") from e

    df = df.dropna(how="all")
    if df.empty:
        raise ErrorDeLectura("El archivo no tiene filas.")
    return df
