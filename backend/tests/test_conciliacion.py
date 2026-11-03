from datetime import date

from app.services.conciliador import conciliar_retenciones
from app.services.importador_arca import leer_arca
from app.services.importador_mayor import leer_mayor
from app.services.impuestos import impuesto_de_texto
from app.services.tools import limpiar_numero


def _arca(importe, fecha, **extra):
    return {"cuit": None, "razon_social": "", "fecha": fecha, "importe": importe,
            "impuesto": "", "certificado": None, **extra}


def _mayor(importe, fecha, **extra):
    return {"cuit": None, "razon_social": "", "fecha": fecha, "importe": importe,
            "impuesto": "", "documento": None, "descripcion": "", "cuenta": "", **extra}


def test_limpiar_numero_formato_argentino():
    assert limpiar_numero("32.110,00") == 32110.0
    assert limpiar_numero("-$ 1.500,50") == -1500.5
    assert limpiar_numero(float("nan")) == 0.0


def test_impuesto_por_palabra_completa():
    assert impuesto_de_texto("Retenciones IVA sufridas") == "IVA"
    assert impuesto_de_texto("767") == "IVA"
    assert impuesto_de_texto("Retenciones Ganancias") == "Ganancias"
    assert impuesto_de_texto("Cuenta activa") is None


def test_lee_arca_con_titulo_y_cp1252(ruta_arca):
    filas = leer_arca(ruta_arca)
    assert len(filas) == 4
    primera = filas[0]
    assert primera["cuit"] == "30500000001"
    assert primera["fecha"] == date(2026, 10, 4)
    assert primera["importe"] == 32110.0
    assert primera["impuesto"] == "IVA"
    assert primera["certificado"] == "0001-00012345"


def test_lee_mayor_separa_aplicaciones_y_saldo_inicial(ruta_mayor):
    lectura = leer_mayor(ruta_mayor)
    assert len(lectura.retenciones) == 3
    assert len(lectura.aplicaciones) == 1
    assert lectura.retenciones[0]["impuesto"] == "IVA"
    assert lectura.retenciones[0]["razon_social"] == "Banco Ejemplo S.A."


def test_cruce_de_punta_a_punta(ruta_arca, ruta_mayor):
    resultado = conciliar_retenciones(leer_arca(ruta_arca), leer_mayor(ruta_mayor).retenciones)
    detalle = resultado["detalle"]

    criterios = {c["razon_social"]: c["criterio"] for c in detalle["conciliados"]}
    assert criterios == {"BANCO EJEMPLO SA": "certificado", "JUAN PEREZ": "razon_social"}

    assert sorted(f["importe"] for f in detalle["no_declarados"]) == [1500.0, 8300.75]
    assert [f["importe"] for f in detalle["falta_certificado"]] == [1500.0]
    assert resultado["resumen"]["importe_conciliado"] == 48210.0


def test_importe_tiene_que_coincidir_al_centavo():
    r = conciliar_retenciones([_arca(100.00, date(2026, 10, 1))], [_mayor(100.01, date(2026, 10, 1))])
    assert r["resumen"]["total_conciliados"] == 0


def test_fuera_de_tolerancia_no_empareja():
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 1))], [_mayor(100.0, date(2026, 10, 20))], tolerancia_dias=5
    )
    assert r["resumen"]["total_conciliados"] == 0


def test_cuits_distintos_no_emparejan():
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 1), cuit="30500000001")],
        [_mayor(100.0, date(2026, 10, 1), cuit="30712345678")],
    )
    assert r["resumen"]["total_conciliados"] == 0


def test_uno_a_uno_con_importes_repetidos():
    arca = [_arca(500.0, date(2026, 10, 1)), _arca(500.0, date(2026, 10, 10))]
    mayor = [_mayor(500.0, date(2026, 10, 10)), _mayor(500.0, date(2026, 10, 1)), _mayor(500.0, date(2026, 10, 2))]
    r = conciliar_retenciones(arca, mayor)
    assert r["resumen"]["total_conciliados"] == 2
    # Cada una con su fecha exacta, no cruzadas en diagonal.
    assert all(c["fecha"] == c["fecha_mayor"] for c in r["detalle"]["conciliados"])
    assert r["resumen"]["total_falta_certificado"] == 1


def test_prefiere_mismo_cuit_aunque_otra_fecha_este_mas_cerca():
    arca = [_arca(100.0, date(2026, 10, 5), cuit="30500000001")]
    mayor = [_mayor(100.0, date(2026, 10, 5)), _mayor(100.0, date(2026, 10, 7), cuit="30500000001")]
    r = conciliar_retenciones(arca, mayor)
    assert r["detalle"]["conciliados"][0]["criterio"] == "cuit"
    assert r["detalle"]["conciliados"][0]["fecha_mayor"] == "2026-10-07"


def test_asientos_del_margen_posterior_no_son_falta_certificado():
    # El mayor se pide hasta unos dias despues del periodo: lo de esos dias que
    # no tiene par es del mes siguiente.
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 30))],
        [_mayor(100.0, date(2026, 11, 2)), _mayor(55.0, date(2026, 11, 3)), _mayor(77.0, date(2026, 10, 15))],
        tolerancia_dias=5,
        hasta_periodo=date(2026, 10, 31),
    )
    assert r["resumen"]["total_conciliados"] == 1
    assert [f["importe"] for f in r["detalle"]["falta_certificado"]] == [77.0]
    assert r["resumen"]["fuera_de_periodo"] == 1


def test_sugiere_mismo_importe_fuera_de_tolerancia():
    r = conciliar_retenciones(
        [_arca(500.0, date(2026, 10, 1), cuit="30712345678")],
        [_mayor(500.0, date(2026, 10, 25), cuit="30712345678")],
        tolerancia_dias=5,
    )
    assert r["resumen"]["total_conciliados"] == 0
    (s,) = r["sugerencias"]
    assert (s["id_arca"], s["id_mayor"], s["fuerza"]) == ("a0", "m0", "alta")
    assert "24 días" in s["motivo"]


def test_sugiere_diferencia_de_centavos_del_mismo_agente():
    r = conciliar_retenciones(
        [_arca(1000.00, date(2026, 10, 1), cuit="30712345678")],
        [_mayor(1000.40, date(2026, 10, 2), cuit="30712345678")],
    )
    (s,) = r["sugerencias"]
    assert s["diferencia_importe"] == -0.4


def test_no_sugiere_agentes_distintos():
    r = conciliar_retenciones(
        [_arca(500.0, date(2026, 10, 1), cuit="30712345678")],
        [_mayor(500.0, date(2026, 10, 25), cuit="20111111112")],
    )
    assert r["sugerencias"] == []


def test_par_manual_y_totales_por_impuesto():
    arca = [_arca(500.0, date(2026, 10, 1)), _arca(80.0, date(2026, 10, 3))]
    mayor = [_mayor(499.0, date(2026, 10, 20)), _mayor(10.0, date(2026, 10, 3))]
    r = conciliar_retenciones(arca, mayor, pares_manuales={0: 0})
    (c,) = r["detalle"]["conciliados"]
    assert (c["criterio"], c["id"], c["id_mayor"], c["diferencia_importe"]) == ("manual", "a0", "m0", 1.0)
    assert r["resumen"]["importe_arca"] == 580.0
    assert r["resumen"]["importe_mayor"] == 509.0
    assert r["resumen"]["diferencia"] == 71.0
    (iva,) = r["por_impuesto"]
    assert (iva["importe_arca"], iva["importe_mayor"], iva["diferencia"]) == (580.0, 509.0, 71.0)
