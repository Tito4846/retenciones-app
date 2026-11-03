from datetime import date

from app.services.conciliador import conciliar_retenciones
from app.services.importador_arca import leer_arca
from app.services.importador_mayor import leer_mayor
from app.services.impuestos import impuesto_de_texto
from app.services.normalizacion import normalizar_arca, normalizar_comprobante
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
    assert criterios == {"BANCO EJEMPLO SA": "comprobante", "JUAN PEREZ": "razon_social"}

    assert sorted(f["importe"] for f in detalle["faltan_en_mayor"]) == [1500.0, 8300.75]
    assert [f["importe"] for f in detalle["faltan_en_arca"]] == [1500.0]
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
    assert r["resumen"]["total_faltan_en_arca"] == 1


def test_prefiere_mismo_cuit_aunque_otra_fecha_este_mas_cerca():
    arca = [_arca(100.0, date(2026, 10, 5), cuit="30500000001")]
    mayor = [_mayor(100.0, date(2026, 10, 5)), _mayor(100.0, date(2026, 10, 7), cuit="30500000001")]
    r = conciliar_retenciones(arca, mayor)
    assert r["detalle"]["conciliados"][0]["criterio"] == "cuit_importe"
    assert r["detalle"]["conciliados"][0]["fecha_mayor"] == "2026-10-07"


def test_asientos_del_margen_posterior_no_son_faltan_en_arca():
    # El mayor se pide hasta unos dias despues del periodo: lo de esos dias que
    # no tiene par es del mes siguiente.
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 30))],
        [_mayor(100.0, date(2026, 11, 2)), _mayor(55.0, date(2026, 11, 3)), _mayor(77.0, date(2026, 10, 15))],
        tolerancia_dias=5,
        hasta_periodo=date(2026, 10, 31),
    )
    assert r["resumen"]["total_conciliados"] == 1
    assert [f["importe"] for f in r["detalle"]["faltan_en_arca"]] == [77.0]
    assert r["resumen"]["fuera_de_periodo"] == 1


def test_sugiere_mismo_importe_fuera_de_tolerancia():
    # Sin CUIT en el asiento no hay clave secundaria: solo importe + fecha.
    r = conciliar_retenciones(
        [_arca(500.0, date(2026, 10, 1), cuit="30712345678", razon_social="PROVEEDOR SRL")],
        [_mayor(500.0, date(2026, 10, 25), razon_social="Proveedor S.R.L.")],
        tolerancia_dias=5,
    )
    assert r["resumen"]["total_conciliados"] == 0
    (s,) = r["sugerencias"]
    assert (s["id_arca"], s["id_mayor"], s["fuerza"]) == ("a0", "m0", "alta")
    assert "24 días" in s["motivo"]


def test_concilia_diferencia_de_centavos_del_mismo_agente():
    # Como en la resolucion a mano: ARCA 18.989,60 y el asiento 18.989.
    r = conciliar_retenciones(
        [_arca(18989.60, date(2026, 4, 8), razon_social="DEL PLATA LOGISTICA S.A.")],
        [_mayor(18989.00, date(2026, 4, 8), razon_social="DEL PLATA LOGISTICA S.A.")],
    )
    (c,) = r["detalle"]["conciliados"]
    assert (c["criterio"], c["diferencia_importe"]) == ("diferencia_menor", 0.6)


def test_diferencia_grande_o_de_otro_agente_no_se_concilia_sola():
    r = conciliar_retenciones(
        [_arca(1000.00, date(2026, 10, 1), cuit="30712345678"), _arca(500.0, date(2026, 10, 1), razon_social="ACME SA")],
        [_mayor(1003.00, date(2026, 10, 2), cuit="30712345678"), _mayor(500.5, date(2026, 10, 1), razon_social="OTRA SRL")],
    )
    assert r["resumen"]["total_conciliados"] == 0


def test_percepcion_no_va_contra_la_cuenta_de_retenciones():
    r = conciliar_retenciones(
        [_arca(5871.6, date(2026, 3, 26), impuesto="Ganancias", tipo="Percepcion")],
        [_mayor(5871.6, date(2026, 3, 26), impuesto="Ganancias", tipo="Retencion")],
    )
    assert r["resumen"]["total_conciliados"] == 0
    assert "cuenta de percepciones" in r["detalle"]["faltan_en_mayor"][0]["nota"]


def test_lo_posterior_a_arca_queda_en_b_con_nota():
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 9, 24))],
        [_mayor(100.0, date(2026, 9, 24)), _mayor(7680.0, date(2026, 10, 22))],
        tolerancia_dias=5,
    )
    (b,) = r["detalle"]["faltan_en_arca"]
    assert "todavía no la haya declarado" in b["nota"]
    assert "posteriores al 24/09/2026" in r["avisos"][0]


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


def test_normaliza_fecha_cuit_importe_y_comprobante():
    r = normalizar_arca({"cuit": "30-71234567-8", "razon_social": "X", "fecha": "04/10/2026",
                         "importe": "1.234,56", "impuesto": "IVA", "certificado": "0001-00012345"})
    assert (r["cuit"], r["fecha"], r["importe"], r["clave_comprobante"]) == (
        "30712345678", date(2026, 10, 4), 1234.56, "100012345")
    assert normalizar_comprobante("000100012345") == normalizar_comprobante("0001-00012345")


def test_clave_principal_comprobante_con_otro_formato():
    # ARCA "0001-00012345", el Libro Mayor de Xubio "Cert. 000100012345": cruzan por comprobante.
    r = conciliar_retenciones(
        [_arca("2.500,00", "01/10/2026", certificado="0001-00012345")],
        [_mayor(2500.0, date(2026, 12, 20), documento="Cert. 000100012345")],
    )
    assert r["detalle"]["conciliados"][0]["criterio"] == "comprobante"


def test_clave_secundaria_cuit_e_importe_sin_importar_la_fecha():
    r = conciliar_retenciones(
        [_arca(500.0, date(2026, 10, 1), cuit="30-71234567-8", certificado="999999")],
        [_mayor(500.0, date(2026, 10, 25), cuit="30712345678", documento="FC 0003-00000077")],
        tolerancia_dias=5,
    )
    assert r["detalle"]["conciliados"][0]["criterio"] == "cuit_importe"


def test_categorias_a_b_c():
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 1), cuit="30712345678"), _arca(300.0, date(2026, 10, 2))],
        [_mayor(100.0, date(2026, 10, 1), cuit="30712345678"), _mayor(50.0, date(2026, 10, 9)),
         _mayor(25.0, date(2026, 10, 9))],
    )
    assert [(c["categoria"], c["nombre"], c["cantidad"], c["importe"]) for c in r["categorias"]] == [
        ("A", "Conciliados", 1, 100.0),
        ("B", "Faltan en ARCA", 2, 75.0),
        ("C", "Faltan en el Mayor", 1, 300.0),
    ]


def test_avisa_si_el_mayor_es_de_otra_cuenta_o_de_otro_mes():
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 5), impuesto="Ganancias")],
        [_mayor(100.0, date(2026, 10, 5), impuesto="Ganancias"), _mayor(50.0, date(2026, 9, 10), impuesto="IVA")],
    )
    cuenta, mes = r["avisos"]
    assert "asientos de IVA" in cuenta and "cuenta de retenciones de Ganancias" in cuenta
    assert "anteriores al 10/2026" in mes


def test_sin_avisos_si_los_dos_son_del_mismo_mes_e_impuesto():
    # Un asiento cargado el 2 del mes siguiente (dentro de la tolerancia) no es otro periodo.
    r = conciliar_retenciones(
        [_arca(100.0, date(2026, 10, 31), impuesto="IVA")],
        [_mayor(100.0, date(2026, 11, 2), impuesto="IVA")],
    )
    assert r["avisos"] == []


def test_mayor_solo_de_la_cuenta_consultada():
    from app.services.importador_mayor import LecturaMayor, solo_cuentas_de

    mayor = LecturaMayor([_mayor(1.0, None, impuesto="IVA"), _mayor(2.0, None, impuesto="Ganancias"),
                          _mayor(3.0, None)], [])
    filtrado, afuera = solo_cuentas_de(mayor, {"Ganancias"})
    assert [m["importe"] for m in filtrado.retenciones] == [2.0, 3.0]
    assert afuera == 1


def test_ignora_total_de_arca_y_apertura_del_mayor(tmp_path):
    from openpyxl import Workbook

    arca = tmp_path / "arca.csv"
    arca.write_text(
        "CUIT Agente Ret./Perc.;Denominación o Razón Social;Impuesto;Fecha Ret./Perc.;Número Certificado;"
        "Descripción Operación;Importe Ret./Perc.\n"
        "30709447846;ICBC;217;26/03/2026;1252415;PERCEPCION;5.871,60\n"
        ";;;;;;5.871,60\n",
        encoding="utf-8",
    )
    (r,) = leer_arca(str(arca))
    assert (r["importe"], r["tipo"]) == (5871.6, "Percepcion")

    libro = Workbook()
    hoja = libro.active
    hoja.append(["Fecha", "Documento", "Cuenta", "Debe", "Haber", "Saldo", "Descripción", "Organización"])
    hoja.append([date(2025, 12, 31), "Saldo Inicial", "1.1.3.18 Retención Ganancias Sufrida", None, None, 0, "", ""])
    hoja.append([date(2026, 1, 1), "Asiento Manual N° 1374", "1.1.3.18 Retención Ganancias Sufrida", 1881700.23,
                 None, 0, "Asiento de apertura patrimonial del ejercicio contable", None])
    hoja.append([date(2026, 1, 8), "Cobranza N° X-0001-00074634", "1.1.3.18 Retención Ganancias Sufrida", 10836.6,
                 None, 0, "", "M&M COMEX CONSULTING S.R.L."])
    hoja.append(["PERCEPCION", "ICBC", None, 5871.6, 0, None, None, None])
    ruta = tmp_path / "mayor.xlsx"
    libro.save(ruta)
    (m,) = leer_mayor(str(ruta)).retenciones
    assert (m["importe"], m["tipo"], m["razon_social"]) == (10836.6, "Retencion", "M&M COMEX CONSULTING S.R.L.")
