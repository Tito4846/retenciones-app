"""
Suite data-driven del motor de conciliación, organizada por código de impuesto de ARCA.

La app NO calcula retenciones (no aplica MNI, escalas ni alícuotas): lee los importes
que el agente ya informó en «Mis Retenciones» y los cruza contra el Libro Mayor de
Xubio. Lo que se prueba acá es lo que puede salir mal con plata real en ese camino:

1. Identificación: que cada código caiga en su impuesto (Ganancias, IVA, PAÍS…), para
   no cruzar una retención de IVA con un asiento de Ganancias del mismo importe.
2. Tipo: retención y percepción no se mezclan.
3. Notas de crédito / anulaciones: el importe negativo conserva el signo y neta exacto.
4. Precisión: todo al centavo con Decimal; ningún total arrastra error de float.
5. Integración: un CSV de ARCA con todos los códigos, importado y conciliado.

La «tabla paramétrica» de ARCA (CODIGOS) es un mock fijo en este archivo: los tests
no dependen del año fiscal ni de lo que ARCA cambie en su formulario.
Esta suite encontró dos huecos, ya corregidos: los totales de ARCA y del Mayor se
sumaban como float, y solo 217 y 767 se reconocían por número.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.services.conciliador import conciliar_retenciones
from app.services.importador_arca import leer_arca
from app.services.impuestos import CODIGOS_ARCA, impuesto_de_texto, tipo_de_texto
from app.services.tools import dec, limpiar_numero

D = Decimal
CENTAVO = D("0.01")


def exacto(valor) -> Decimal:
    """El valor tal como lo devuelve el motor, sin redondear: un float con arrastre (0.30000000000000004) no pasa."""
    return D(str(valor))


# ----------------------------------------------------------------------
# Tabla paramétrica simulada de ARCA
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Codigo:
    codigo: str
    familia: str  # el impuesto común con la cuenta de Xubio
    descripcion: str  # como la escribe ARCA en «Descripción Impuesto»
    tipo: str  # el que admite el formulario de Mis Retenciones
    cuenta_xubio: str  # la cuenta del Libro Mayor donde se registra
    por_codigo: bool = True  # se reconoce solo con el número (sin descripción)


CODIGOS = [
    # Familia Ganancias
    Codigo("210", "Ganancias", "Impuesto a las Ganancias - RG 830", "Retencion", "Retenciones de Ganancias sufridas"),
    Codigo("217", "Ganancias", "Impuesto a las Ganancias", "Retencion", "Retenciones de Ganancias sufridas"),
    Codigo("787", "Ganancias", "Ganancias - Rentas de cuarta categoría", "Retencion", "Retenciones de Ganancias sufridas"),
    Codigo("218", "Ganancias", "Ganancias - Beneficiarios del exterior", "Retencion", "Retenciones de Ganancias sufridas"),
    # Regímenes especiales: solo por su descripción (no está confirmado si ARCA los informa como Ganancias)
    Codigo("222", "Donaciones", "Donaciones", "Retencion", "Retenciones sobre donaciones sufridas", por_codigo=False),
    Codigo("466", "Premios", "Premios de juegos y concursos deportivos", "Retencion", "Retenciones sobre premios sufridas", por_codigo=False),
    # Familia IVA
    Codigo("216", "IVA", "IVA - SIRE", "Retencion", "Retenciones de IVA sufridas"),
    Codigo("767", "IVA", "Impuesto al Valor Agregado", "Retencion", "Retenciones de IVA sufridas"),
    # Patrimoniales, transferencias y consumo
    Codigo("172", "Transferencia de Inmuebles", "Impuesto a la transferencia de inmuebles", "Retencion", "Retenciones ITI sufridas"),
    Codigo("219", "Bienes Personales", "Bienes Personales - Acciones y participaciones", "Retencion", "Retenciones Bienes Personales sufridas"),
    Codigo("939", "Impuesto PAIS", "Impuesto PAIS", "Percepcion", "Percepciones Impuesto PAIS sufridas"),
]
POR_CODIGO = {c.codigo: c for c in CODIGOS}
IDS = [f"{c.codigo}-{c.familia}" for c in CODIGOS]
POR_NUMERO = [c for c in CODIGOS if c.por_codigo]
IDS_POR_NUMERO = [f"{c.codigo}-{c.familia}" for c in POR_NUMERO]

# Antes solo 217 y 767 se reconocían por número (lo encontró esta suite).


# ----------------------------------------------------------------------
# Fábricas de movimientos (lo que devuelven los importadores)
# ----------------------------------------------------------------------

FECHA = date(2026, 10, 15)


def arca(importe, codigo: Codigo, *, cert="0001-00012345", cuit="30712345678", fecha=FECHA, tipo=None, impuesto=None):
    return {
        "cuit": cuit, "razon_social": "AGENTE DE PRUEBA SA", "fecha": fecha, "importe": importe,
        "impuesto": impuesto if impuesto is not None else (impuesto_de_texto(codigo.codigo, codigo.descripcion) or codigo.descripcion),
        "certificado": cert, "tipo": tipo if tipo is not None else codigo.tipo,
    }


def mayor(importe, codigo: Codigo, *, documento="REC 0001-00012345", cuit="30712345678", fecha=FECHA, cuenta=None):
    cuenta = cuenta or codigo.cuenta_xubio
    return {
        "cuit": cuit, "razon_social": "Agente de Prueba S.A.", "fecha": fecha, "importe": importe,
        "impuesto": impuesto_de_texto(cuenta) or "", "tipo": tipo_de_texto(cuenta),
        "documento": documento, "descripcion": "", "cuenta": cuenta,
    }


def resumen_de(arca_movs, mayor_movs):
    return conciliar_retenciones(arca_movs, mayor_movs, tolerancia_dias=5)


# ======================================================================
# 1. Identificación del impuesto por código y descripción
# ======================================================================


class TestIdentificacionDelImpuesto:
    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    def test_con_descripcion_cae_en_su_familia(self, c: Codigo):
        assert impuesto_de_texto(c.codigo, c.descripcion) == c.familia

    @pytest.mark.parametrize("c", POR_NUMERO, ids=IDS_POR_NUMERO)
    def test_solo_con_el_codigo_cae_en_su_familia(self, c: Codigo):
        """Hay exportaciones de ARCA sin la columna «Descripción Impuesto»."""
        assert impuesto_de_texto(c.codigo) == c.familia

    def test_la_tabla_del_motor_cubre_todos_los_codigos_por_numero(self):
        assert {c.codigo: c.familia for c in POR_NUMERO}.items() <= CODIGOS_ARCA.items()

    def test_el_bot_sigue_consultando_por_defecto_solo_los_principales(self):
        """Reconocer más códigos no cambia qué consulta el bot: 217 y 767, como antes."""
        from app.bots.misret import impuestos_a_consultar

        opciones = [{"codigo": c.codigo, "descripcion": c.descripcion} for c in CODIGOS]
        assert impuestos_a_consultar(opciones) == ["217", "767"]
        assert impuestos_a_consultar(opciones, {"IVA"}) == ["767"]

    @pytest.mark.parametrize("c", [c for c in CODIGOS if not c.por_codigo], ids=[i for c, i in zip(CODIGOS, IDS) if not c.por_codigo])
    def test_premios_y_donaciones_no_se_asignan_por_numero(self, c: Codigo):
        assert impuesto_de_texto(c.codigo) is None

    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    def test_la_cuenta_de_xubio_es_compatible_con_su_codigo(self, c: Codigo):
        """Mismo importe, misma fecha, mismo certificado: concilia por comprobante."""
        r = resumen_de([arca("15000.00", c)], [mayor("15000.00", c)])
        assert r["resumen"]["total_conciliados"] == 1
        assert r["detalle"]["conciliados"][0]["criterio"] == "comprobante"

    @pytest.mark.parametrize("c", POR_NUMERO, ids=IDS_POR_NUMERO)
    def test_codigo_sin_descripcion_igual_concilia_con_su_cuenta(self, c: Codigo):
        sin_descripcion = arca("15000.00", c, impuesto=impuesto_de_texto(c.codigo) or c.codigo)
        r = resumen_de([sin_descripcion], [mayor("15000.00", c)])
        assert r["resumen"]["total_conciliados"] == 1


# ======================================================================
# 2. Familias distintas nunca se cruzan (aunque el importe coincida)
# ======================================================================

PARES_DE_FAMILIAS = [
    ("217", "767"),  # Ganancias SICORE vs IVA SICORE
    ("210", "216"),  # Ganancias RG 830 vs IVA SIRE
    ("787", "767"),
    ("218", "216"),
    ("939", "767"),  # PAÍS vs IVA
    ("219", "217"),  # Bienes Personales vs Ganancias
    ("172", "217"),  # ITI vs Ganancias
    ("466", "217"),  # Premios vs Ganancias
    ("222", "219"),  # Donaciones vs Bienes Personales
    ("767", "217"),
]


class TestSeparacionEntreImpuestos:
    @pytest.mark.parametrize("de_arca,de_mayor", PARES_DE_FAMILIAS)
    @pytest.mark.parametrize("importe", ["1500.00", "32110.00", "0.01"])
    def test_mismo_importe_otra_familia_no_concilia(self, de_arca, de_mayor, importe):
        r = resumen_de([arca(importe, POR_CODIGO[de_arca], cert="999")], [mayor(importe, POR_CODIGO[de_mayor], documento="REC 1")])
        assert r["resumen"]["total_conciliados"] == 0
        assert r["resumen"]["total_faltan_en_mayor"] == 1
        assert r["resumen"]["total_faltan_en_arca"] == 1
        # El control de la DDJJ se arma por impuesto: cada uno con su diferencia.
        por_impuesto = {f["impuesto"]: f for f in r["por_impuesto"]}
        assert exacto(por_impuesto[POR_CODIGO[de_arca].familia]["diferencia"]) == D(importe)
        assert exacto(por_impuesto[POR_CODIGO[de_mayor].familia]["diferencia"]) == -D(importe)

    @pytest.mark.parametrize("c", [POR_CODIGO["939"]], ids=["939-percepcion-PAIS"])
    def test_percepcion_no_va_contra_la_cuenta_de_retenciones(self, c: Codigo):
        r = resumen_de([arca("8000.00", c, cert="1")], [mayor("8000.00", c, documento="X", cuenta="Retenciones Impuesto PAIS sufridas")])
        assert r["resumen"]["total_conciliados"] == 0

    @pytest.mark.parametrize(
        "texto,tipo",
        [
            ("RETENCION", "Retencion"),
            ("PERCEPCION", "Percepcion"),
            ("Percepción de Ganancias Sufrida", "Percepcion"),
            ("Retenciones de IVA sufridas", "Retencion"),
            ("Retención y/o Percepción", ""),  # ambiguo: no se adivina
            ("", ""),
        ],
    )
    def test_tipo_desde_el_texto(self, texto, tipo):
        assert tipo_de_texto(texto) == tipo


# ======================================================================
# 3. Notas de crédito y anulaciones (importe negativo)
# ======================================================================


class TestNotasDeCredito:
    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    def test_anulacion_neta_exacto_contra_la_original(self, c: Codigo):
        """ARCA informa la retención y su anulación: lo neto es cero, al centavo."""
        movs = [arca("12345.67", c, cert="100"), arca("-12345.67", c, cert="101")]
        r = resumen_de(movs, [])
        assert exacto(r["resumen"]["importe_arca"]) == D("0.00")
        assert exacto(r["resumen"]["diferencia"]) == D("0.00")

    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    def test_negativo_no_concilia_con_un_asiento_positivo(self, c: Codigo):
        r = resumen_de([arca("-5000.00", c, cert="200")], [mayor("5000.00", c, documento="REC 7")])
        assert r["resumen"]["total_conciliados"] == 0
        assert exacto(r["resumen"]["diferencia"]) == D("-10000.00")

    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    def test_negativo_concilia_con_su_contrapartida_negativa(self, c: Codigo):
        r = resumen_de([arca("-5000.00", c, cert="0001-00000300")], [mayor("-5000.00", c, documento="NC 0001-00000300")])
        assert r["resumen"]["total_conciliados"] == 1
        assert exacto(r["resumen"]["diferencia"]) == D("0.00")

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("-1.500,00", D("-1500.00")),
            ("1.500,00-", D("-1500.00")),  # signo al final, como exportan algunos sistemas
            ("-$ 29.000,00", D("-29000.00")),
            ("-0,01", D("-0.01")),
        ],
    )
    def test_lectura_de_importes_negativos(self, texto, esperado):
        assert dec(limpiar_numero(texto)) == esperado


# ======================================================================
# 4. Precisión: cero tolerancia a errores de punto flotante
# ======================================================================

# Montos elegidos para romper la aritmética de float: 0.1 + 0.2, tercios, millones con centavos.
MATRICES_DE_CENTAVOS = [
    pytest.param(["0.10", "0.20"], id="0.1+0.2"),
    pytest.param(["33.33", "33.33", "33.34"], id="tercios"),
    pytest.param(["0.01"] * 100, id="cien-centavos"),
    pytest.param(["1234567.89", "9876543.21", "0.07"], id="millones"),
    pytest.param(["9999999999.99", "0.01"], id="diez-mil-millones"),
    pytest.param(["18989.60", "-18989.60", "0.03"], id="con-anulacion"),
]


# Bug que encontró esta suite (ya corregido): importe_arca e importe_mayor se
# sumaban como float y llegaban con arrastre (0.30000000000000004) a la API.


class TestPrecisionDecimal:
    @pytest.mark.parametrize("montos", MATRICES_DE_CENTAVOS)
    @pytest.mark.parametrize("codigo", ["217", "767", "939"])
    def test_totales_exactos_cuando_todo_concilia(self, montos, codigo):
        c = POR_CODIGO[codigo]
        movs_arca = [arca(m, c, cert=f"0001-{i:08d}") for i, m in enumerate(montos)]
        movs_mayor = [mayor(m, c, documento=f"REC 0001-{i:08d}") for i, m in enumerate(montos)]
        r = resumen_de(movs_arca, movs_mayor)
        esperado = sum((D(m) for m in montos), D("0"))
        assert r["resumen"]["total_conciliados"] == len(montos)
        assert exacto(r["resumen"]["importe_arca"]) == esperado
        assert exacto(r["resumen"]["importe_mayor"]) == esperado
        assert exacto(r["resumen"]["diferencia"]) == D("0.00")

    @pytest.mark.parametrize("montos", MATRICES_DE_CENTAVOS)
    def test_importe_arca_exacto_mezclando_conciliados_y_faltantes(self, montos):
        """importe_arca = conciliados + faltan en el Mayor: la suma de los dos grupos no puede arrastrar float."""
        c = POR_CODIGO["217"]
        # El primero concilia; el resto queda «falta en el Mayor».
        movs_arca = [arca(m, c, cert=f"0001-{i:08d}") for i, m in enumerate(montos)]
        movs_mayor = [mayor(montos[0], c, documento="REC 0001-00000000")]
        r = resumen_de(movs_arca, movs_mayor)
        assert exacto(r["resumen"]["importe_arca"]) == sum((D(m) for m in montos), D("0"))
        assert exacto(r["resumen"]["diferencia"]) == sum((D(m) for m in montos[1:]), D("0"))

    @pytest.mark.parametrize(
        "en_arca,en_mayor,diferencia",
        [
            ("18989.60", "18989.00", "0.60"),  # redondeo al cargar el asiento
            ("1000.01", "1000.00", "0.01"),
            ("1000.00", "1000.99", "-0.99"),
        ],
    )
    def test_diferencia_menor_queda_a_la_vista_al_centavo(self, en_arca, en_mayor, diferencia):
        c = POR_CODIGO["767"]
        r = resumen_de([arca(en_arca, c, cert="1")], [mayor(en_mayor, c, documento="X")])
        conciliado = r["detalle"]["conciliados"][0]
        assert conciliado["criterio"] == "diferencia_menor"
        assert exacto(conciliado["diferencia_importe"]) == D(diferencia)
        assert exacto(r["resumen"]["diferencia"]) == D(diferencia)

    @pytest.mark.parametrize("en_arca,en_mayor", [("1000.00", "1001.01"), ("5000.00", "4998.50")])
    def test_mas_de_un_peso_no_es_redondeo(self, en_arca, en_mayor):
        c = POR_CODIGO["767"]
        r = resumen_de([arca(en_arca, c, cert="1")], [mayor(en_mayor, c, documento="X")])
        assert r["resumen"]["total_conciliados"] == 0

    @pytest.mark.parametrize("montos", MATRICES_DE_CENTAVOS)
    def test_por_impuesto_suma_igual_que_el_resumen(self, montos):
        """El cuadro por impuesto y el resumen general no pueden diferir ni un centavo."""
        movs_arca = [arca(m, POR_CODIGO["217" if i % 2 else "767"], cert=f"0001-{i:08d}") for i, m in enumerate(montos)]
        r = resumen_de(movs_arca, [])
        suma = sum((exacto(f["importe_arca"]) for f in r["por_impuesto"]), D("0"))
        assert suma == exacto(r["resumen"]["importe_arca"])


# ======================================================================
# 5. Integración: CSV de Mis Retenciones con todos los códigos
# ======================================================================

ENCABEZADO = "CUIT Agente Ret./Perc.;Denominación;Impuesto;Descripción Impuesto;Régimen;Fecha Ret./Perc.;Número Certificado;Descripción Operación;Importe Ret./Perc."


def _csv(filas: list[tuple[Codigo, str, str]]) -> str:
    lineas = ["Mis Retenciones - Período 10/2026", ENCABEZADO]
    for c, cert, importe in filas:
        operacion = "PERCEPCION" if c.tipo == "Percepcion" else "RETENCION"
        lineas.append(f"30-71234567-8;AGENTE DE PRUEBA SA;{c.codigo};{c.descripcion};{c.descripcion};15/10/2026;{cert};{operacion};{importe}")
    return "\n".join(lineas) + "\n"


class TestIntegracionMisRetenciones:
    @pytest.fixture
    def archivo(self, tmp_path):
        def escribir(filas):
            ruta = tmp_path / "mis_retenciones.csv"
            ruta.write_text(_csv(filas), encoding="cp1252")
            return str(ruta)

        return escribir

    @pytest.mark.parametrize("c", CODIGOS, ids=IDS)
    @pytest.mark.parametrize("texto,esperado", [("1.234,56", "1234.56"), ("0,01", "0.01"), ("-2.500,00", "-2500.00")])
    def test_lee_cada_codigo_con_su_importe_exacto(self, archivo, c, texto, esperado):
        filas = leer_arca(archivo([(c, "0001-00000001", texto)]))
        assert len(filas) == 1
        assert exacto(filas[0]["importe"]) == D(esperado)
        assert filas[0]["tipo"] == c.tipo
        if c.familia:
            assert filas[0]["impuesto"] == c.familia

    def test_todos_los_codigos_de_punta_a_punta(self, archivo):
        """Un mes con todos los regímenes: cada uno concilia contra su cuenta y los totales cierran al centavo."""
        filas = [(c, f"0001-{i:08d}", f"{1000 + i},{i:02d}") for i, c in enumerate(CODIGOS)]
        # Una nota de crédito de IVA SIRE que anula parte del mes.
        filas.append((POR_CODIGO["216"], "0001-00009999", "-150,25"))
        movs_arca = leer_arca(archivo(filas))

        movs_mayor = [mayor(f"{1000 + i}.{i:02d}", c, documento=f"REC 0001-{i:08d}") for i, c in enumerate(CODIGOS)]
        r = resumen_de(movs_arca, movs_mayor)

        esperado_arca = sum((D(f"{1000 + i}.{i:02d}") for i in range(len(CODIGOS))), D("0")) - D("150.25")
        assert r["resumen"]["total_conciliados"] == len(CODIGOS)
        assert r["resumen"]["total_faltan_en_mayor"] == 1  # la nota de crédito sin asiento
        assert exacto(r["resumen"]["importe_arca"]) == esperado_arca
        assert exacto(r["resumen"]["diferencia"]) == D("-150.25")
        # Cada familia tiene su renglón en el control por impuesto y suman lo mismo que el resumen.
        por_impuesto = {f["impuesto"]: f for f in r["por_impuesto"]}
        assert {"Ganancias", "IVA"} <= set(por_impuesto)
        assert exacto(por_impuesto["IVA"]["diferencia"]) == D("-150.25")
        assert exacto(por_impuesto["Ganancias"]["diferencia"]) == D("0.00")
        assert sum((exacto(f["importe_arca"]) for f in r["por_impuesto"]), D("0")) == esperado_arca
