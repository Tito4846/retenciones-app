import pytest

from app.bots import conexiones
from app.services.cuentas_xubio import elegir_cuenta, elegir_cuentas
from app.services.importador_mayor import LecturaMayor

PLAN = [
    "1.1.4.01 - Retenciones de Ganancias Sufridas",
    "1.1.4.02 - Percepciones de Ganancias Sufridas",
    "1.1.4.03 - Retención de IVA sufrida",
    "1.1.4.04 - Percepciones de IVA sufridas",
    "2.1.3.01 - Retenciones de Ganancias a depositar",
    "4.1.1.01 - Ganancias por diferencia de cambio",
]


def test_elige_la_cuenta_del_plan_por_impuesto_y_tipo():
    assert elegir_cuenta(PLAN, "Ganancias", "Retencion") == PLAN[0]
    assert elegir_cuenta(PLAN, "IVA", "Percepcion") == PLAN[3]
    assert elegir_cuentas(PLAN, "IVA", "RetencionPercepcion") == [PLAN[2], PLAN[3]]


def test_no_elige_las_que_retiene_la_empresa_ni_otras_cuentas():
    assert elegir_cuenta(PLAN[4:], "Ganancias", "Retencion") is None
    assert elegir_cuenta(["Caja", "Banco Galicia"], "IVA", "Retencion") is None


def _asiento(cuenta, importe, impuesto):
    return {"cuenta": cuenta, "importe": importe, "impuesto": impuesto}


MAYOR = LecturaMayor([_asiento(c, i, imp) for i, (c, imp) in enumerate(
    zip(PLAN[:4] + ["Retenciones SUSS sufridas"], ["Ganancias", "Ganancias", "IVA", "IVA", "SUSS"]), start=1
)], [])


def _importes(impuestos, tipo, mayor=MAYOR):
    return [m["importe"] for m in conexiones.cuenta_de_la_consulta(mayor, impuestos, tipo).retenciones]


def test_del_reporte_entero_quedan_las_cuentas_de_la_consulta():
    assert _importes({"Ganancias"}, "Retencion") == [1]
    assert _importes({"IVA"}, "Percepcion") == [4]
    assert _importes({"Ganancias"}, "RetencionPercepcion") == [1, 2]


def test_sin_cuenta_reconocible_filtra_por_impuesto():
    assert _importes({"SUSS"}, "Retencion") == [5]
    otro_plan = LecturaMayor([_asiento("Gcias. a recuperar", 9.0, "Ganancias"), _asiento("IVA a recuperar", 8.0, "IVA")], [])
    assert _importes({"Ganancias"}, "Retencion", otro_plan) == [9.0]
    assert _importes({None}, "Retencion") == [1, 2, 3, 4, 5]


def test_en_cuenta_se_escribe_el_comienzo_del_nombre():
    from app.services.cuentas_xubio import terminos_de_busqueda

    assert terminos_de_busqueda("Ganancias", "Retencion") == ["Retención", "Retencion", "Ganancia"]
    assert terminos_de_busqueda("Ganancias", "Percepcion")[0] == "Percepción"
    nombres = ["Retención Ganancias Sufrida", "Retención IVA Sufrida", "Percepción de Ganancias Sufrida"]
    assert elegir_cuentas(nombres, "Ganancias", "RetencionPercepcion") == [nombres[0], nombres[2]]


# Un plan de cuentas con los regímenes que no son Ganancias ni IVA.
PLAN_OTROS = [
    "1.1.4.10 - Percepciones Impuesto PAIS sufridas",
    "1.1.4.11 - Retenciones Bienes Personales Acciones y Participaciones",
    "1.1.4.12 - Retenciones ITI sufridas",
    "1.1.4.13 - Retenciones sobre premios sufridas",
    "1.1.4.14 - Retención Donaciones a recuperar",
    "2.1.3.05 - Percepciones Impuesto PAIS a depositar",
    "6.1.1.01 - Donaciones",  # gasto: no es una retención
    "1.1.4.01 - Retenciones de Ganancias Sufridas",
]


@pytest.mark.parametrize(
    "impuesto,tipo,esperada",
    [
        ("Impuesto PAIS", "Percepcion", PLAN_OTROS[0]),
        ("Bienes Personales", "Retencion", PLAN_OTROS[1]),
        ("Transferencia de Inmuebles", "Retencion", PLAN_OTROS[2]),
        ("Premios", "Retencion", PLAN_OTROS[3]),
        ("Donaciones", "Retencion", PLAN_OTROS[4]),
        ("Ganancias", "Retencion", PLAN_OTROS[7]),
    ],
)
def test_elige_la_cuenta_de_los_otros_impuestos(impuesto, tipo, esperada):
    assert elegir_cuenta(PLAN_OTROS, impuesto, tipo) == esperada


@pytest.mark.parametrize("impuesto,tipo", [("Impuesto PAIS", "Retencion"), ("Bienes Personales", "Percepcion")])
def test_sin_cuenta_del_tipo_no_elige_otra(impuesto, tipo):
    assert elegir_cuenta(PLAN_OTROS, impuesto, tipo) is None
