"""
Cruce de las retenciones de ARCA contra el Libro Mayor de Xubio.

Sigue las reglas del conciliador bancario:

* uno a uno: una retencion de ARCA no puede quedar con dos asientos ni al reves;
* el importe tiene que coincidir AL CENTAVO. Una diferencia de un centavo sale
  en las dos bandejas en vez de conciliar sola: si ARCA y la contabilidad no
  dicen lo mismo, que lo mire una persona;
* la fecha puede diferir hasta TOLERANCIA_DIAS (el asiento se carga con la
  fecha del cobro, no la del certificado).

Y suma lo que una retencion trae y un movimiento bancario no:

1. Si el numero de certificado de ARCA aparece en el documento o la descripcion
   del asiento, ese par gana aunque la fecha este lejos.
2. El impuesto tiene que ser compatible: una retencion de IVA no se empareja
   con un asiento de la cuenta de Ganancias del mismo importe.
3. Entre varios candidatos del mismo importe se prefiere el del mismo CUIT,
   despues el de razon social parecida y por ultimo el de fecha mas cercana.
   Si los dos lados tienen CUIT y no coinciden, no hay par.

Bandejas:
  conciliados        -> esta en ARCA y en el mayor.
  falta_certificado  -> esta registrada en el mayor pero ARCA no la informa
                        (no hay certificado que la respalde).
  no_declarados      -> ARCA la informa pero no esta registrada en el mayor.
"""

import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from app.core.config import settings
from app.services.tools import dec, formatear_cuit, solo_digitos

logger = logging.getLogger(__name__)

SIMILITUD_RAZON_SOCIAL = 0.6
_SUFIJOS_SOCIETARIOS = re.compile(r"\b(S\.?A\.?U?|S\.?R\.?L\.?|S\.?A\.?S\.?|S\.?C\.?A\.?|LTDA|SOCIEDAD ANONIMA)\b\.?")

CRITERIO_CERTIFICADO = "certificado"
CRITERIO_CUIT = "cuit"
CRITERIO_RAZON_SOCIAL = "razon_social"
CRITERIO_IMPORTE_FECHA = "importe_fecha"
CRITERIO_MANUAL = "manual"


def _palabras_nombre(nombre: str) -> list[str]:
    sin_acentos = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    sin_sufijo = _SUFIJOS_SOCIETARIOS.sub(" ", sin_acentos.upper())
    return re.findall(r"[A-Z0-9]+", sin_sufijo)


def _similitud(a: str, b: str) -> float:
    """
    Parecido entre dos razones sociales, de 0 a 1.

    Se compara tambien con las palabras ordenadas: ARCA escribe "PEREZ JUAN" y
    en Xubio la organizacion quedo como "Juan Pérez".
    """
    pa, pb = _palabras_nombre(a), _palabras_nombre(b)
    if not pa or not pb:
        return 0.0
    juntas_a, juntas_b = "".join(pa), "".join(pb)
    if juntas_a in juntas_b or juntas_b in juntas_a:
        return 1.0
    return max(
        SequenceMatcher(None, juntas_a, juntas_b).ratio(),
        SequenceMatcher(None, "".join(sorted(pa)), "".join(sorted(pb))).ratio(),
    )


def _impuestos_compatibles(arca: dict, mayor: dict) -> bool:
    return not arca["impuesto"] or not mayor["impuesto"] or arca["impuesto"] == mayor["impuesto"]


def _certificado_en_asiento(arca: dict, mayor: dict) -> bool:
    """
    El numero de certificado de ARCA escrito en el documento o la descripcion
    del asiento. Se comparan los numeros sin ceros a la izquierda: ARCA informa
    "0001-00012345" y en Xubio se carga "Cert. 12345" (o al reves).
    """
    claves = _numeros(arca.get("certificado"))
    if claves:
        # Tambien el ultimo tramo solo: "0001-00012345" se suele cargar "12345".
        grupos = re.findall(r"\d+", str(arca.get("certificado")))
        claves.add(grupos[-1].lstrip("0"))
    claves = {c for c in claves if len(c) >= 4}
    if not claves:
        return False
    en_asiento = _numeros(mayor.get("documento")) | _numeros(mayor.get("descripcion"))
    return bool(claves & en_asiento)


def _numeros(valor) -> set[str]:
    """
    Los numeros de un texto sin ceros a la izquierda, sueltos y unidos.

    Se compara por numero entero y no por substring: el certificado "12345" no
    tiene que aparecer adentro de una factura "112345".
    """
    grupos = [g.lstrip("0") for g in re.findall(r"\d+", str(valor or ""))]
    grupos = [g for g in grupos if g]
    unidos = solo_digitos(valor).lstrip("0")
    return set(grupos) | ({unidos} if unidos else set())


def _distancia_dias(a: date | None, b: date | None) -> int | None:
    if a is None or b is None:
        return None
    return abs((a - b).days)


def _a_salida(mov: dict, **extra) -> dict:
    """La forma que espera el front: cuit, razon_social, fecha, importe, impuesto."""
    return {
        "cuit": formatear_cuit(mov.get("cuit")),
        "razon_social": mov.get("razon_social") or "",
        "fecha": mov["fecha"].isoformat() if mov.get("fecha") else None,
        "importe": float(mov["importe"]),
        "impuesto": mov.get("impuesto") or "",
        **extra,
    }


def _pesos(importe: Decimal) -> str:
    """Decimal('1234.5') -> '$ 1.234,50' (como se lee en Argentina)."""
    return "$ " + f"{importe:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


# Hasta cuanto se considera "diferencia chica" entre ARCA y el asiento para
# sugerir el par: un redondeo o un error de tipeo, no otra retencion.
DIFERENCIA_SUGERIBLE_ABS = Decimal("1.00")
DIFERENCIA_SUGERIBLE_REL = Decimal("0.01")
# Mismo importe pero fuera de la tolerancia: hasta cuantos dias se sugiere.
DIAS_SUGERIBLES = 60


def id_arca(i: int) -> str:
    return f"a{i}"


def id_mayor(j: int) -> str:
    return f"m{j}"


def conciliar_retenciones(
    arca: list[dict],
    mayor: list[dict],
    tolerancia_dias: int | None = None,
    hasta_periodo: date | None = None,
    pares_manuales: dict[int, int] | None = None,
) -> dict:
    """
    `hasta_periodo`: ultimo dia del periodo consultado. El mayor se pide unos
    dias mas (la tolerancia) para atrapar los asientos cargados con la fecha
    del cobro; los de esos dias que no se emparejan son retenciones del periodo
    siguiente y no van a "falta certificado".

    `pares_manuales`: {indice ARCA: indice mayor} que la persona emparejo a
    mano. Van antes que todo, aunque el importe o la fecha no coincidan.
    """
    tolerancia = settings.TOLERANCIA_DIAS if tolerancia_dias is None else tolerancia_dias

    indice_mayor: dict[Decimal, list[int]] = {}
    for i, m in enumerate(mayor):
        indice_mayor.setdefault(dec(m["importe"]), []).append(i)

    usados_mayor: set[int] = set()
    pares: dict[int, tuple[int, str]] = {}  # indice arca -> (indice mayor, criterio)

    # 0) Lo que la persona ya decidio.
    for i, j in (pares_manuales or {}).items():
        if 0 <= i < len(arca) and 0 <= j < len(mayor) and i not in pares and j not in usados_mayor:
            usados_mayor.add(j)
            pares[i] = (j, CRITERIO_MANUAL)

    def candidatos(i_arca: int) -> list[int]:
        r = arca[i_arca]
        return [
            j
            for j in indice_mayor.get(dec(r["importe"]), [])
            if j not in usados_mayor and _impuestos_compatibles(r, mayor[j])
        ]

    # 1) Por numero de certificado: es la prueba mas fuerte, va primero para
    #    que un par por fecha no le robe el asiento.
    for i, r in enumerate(arca):
        if i in pares:
            continue
        for j in candidatos(i):
            if _certificado_en_asiento(r, mayor[j]):
                usados_mayor.add(j)
                pares[i] = (j, CRITERIO_CERTIFICADO)
                break

    # 2) Por importe y fecha, desempatando por CUIT y razon social. Se recorre
    #    ARCA por fecha para que las retenciones viejas tomen los asientos
    #    viejos y no se crucen en diagonal.
    orden = sorted(
        (i for i in range(len(arca)) if i not in pares),
        key=lambda i: (arca[i]["fecha"] is None, arca[i]["fecha"] or date.min),
    )
    for i in orden:
        r = arca[i]
        mejor = None
        for j in candidatos(i):
            m = mayor[j]
            distancia = _distancia_dias(r["fecha"], m["fecha"])
            if distancia is None or distancia > tolerancia:
                continue
            if _cuits_distintos(r, m):
                continue

            if r.get("cuit") and r["cuit"] == m.get("cuit"):
                criterio, prioridad = CRITERIO_CUIT, 0
            elif _similitud(r["razon_social"], m["razon_social"]) >= SIMILITUD_RAZON_SOCIAL:
                criterio, prioridad = CRITERIO_RAZON_SOCIAL, 1
            else:
                criterio, prioridad = CRITERIO_IMPORTE_FECHA, 2

            clave = (prioridad, distancia, j)
            if mejor is None or clave < mejor[0]:
                mejor = (clave, j, criterio)

        if mejor is not None:
            _, j, criterio = mejor
            usados_mayor.add(j)
            pares[i] = (j, criterio)

    conciliados = []
    for i in sorted(pares, key=lambda i: (arca[i]["fecha"] is None, arca[i]["fecha"] or date.min)):
        j, criterio = pares[i]
        r, m = arca[i], mayor[j]
        conciliados.append(
            _a_salida(
                r,
                id=id_arca(i),
                id_mayor=id_mayor(j),
                razon_social=r["razon_social"] or m["razon_social"],
                certificado=r.get("certificado"),
                criterio=criterio,
                fecha_mayor=m["fecha"].isoformat() if m["fecha"] else None,
                diferencia_dias=_distancia_dias(r["fecha"], m["fecha"]),
                importe_mayor=float(m["importe"]),
                diferencia_importe=float(dec(r["importe"]) - dec(m["importe"])),
                documento_mayor=m.get("documento"),
                cuenta_mayor=m.get("cuenta"),
                impuesto_mayor=m.get("impuesto") or "",
            )
        )

    no_declarados = [
        _a_salida(r, id=id_arca(i), certificado=r.get("certificado"), regimen=r.get("regimen"))
        for i, r in enumerate(arca)
        if i not in pares
    ]

    def del_periodo_siguiente(m: dict) -> bool:
        return hasta_periodo is not None and m["fecha"] is not None and m["fecha"] > hasta_periodo

    sin_par = [j for j in range(len(mayor)) if j not in usados_mayor]
    del_periodo = [j for j in sin_par if not del_periodo_siguiente(mayor[j])]
    falta_certificado = [
        _a_salida(
            mayor[j],
            id=id_mayor(j),
            documento=mayor[j].get("documento"),
            cuenta=mayor[j].get("cuenta"),
            descripcion=mayor[j].get("descripcion"),
        )
        for j in del_periodo
    ]
    fuera_de_periodo = len(sin_par) - len(del_periodo)

    sugerencias = _sugerencias(
        arca, mayor, [i for i in range(len(arca)) if i not in pares], del_periodo, tolerancia
    )

    def total(filas: list[dict], campo: str = "importe") -> float:
        return float(sum((dec(f[campo]) for f in filas), Decimal("0")))

    importe_arca = total(conciliados) + total(no_declarados)
    importe_mayor = total(conciliados, "importe_mayor") + total(falta_certificado)
    resumen = {
        "total_conciliados": len(conciliados),
        "total_falta_certificado": len(falta_certificado),
        "total_no_declarados": len(no_declarados),
        "total_manuales": sum(1 for c in conciliados if c["criterio"] == CRITERIO_MANUAL),
        "total_sugerencias": len(sugerencias),
        "importe_conciliado": total(conciliados),
        "importe_falta_certificado": total(falta_certificado),
        "importe_no_declarado": total(no_declarados),
        # Lo que dice cada lado y cuanto difieren: es el control de la DDJJ.
        "importe_arca": importe_arca,
        "importe_mayor": importe_mayor,
        "diferencia": float(dec(importe_arca) - dec(importe_mayor)),
        "total_arca": len(arca),
        "total_mayor": len(conciliados) + len(falta_certificado),
        "fuera_de_periodo": fuera_de_periodo,
        "tolerancia_dias": tolerancia,
        "hasta_periodo": hasta_periodo.isoformat() if hasta_periodo else None,
    }
    logger.info(
        "Cruce: %d conciliados (%d a mano), %d falta certificado, %d no declarados, %d sugerencias",
        len(conciliados),
        resumen["total_manuales"],
        len(falta_certificado),
        len(no_declarados),
        len(sugerencias),
    )
    return {
        "resumen": resumen,
        "por_impuesto": _por_impuesto(conciliados, falta_certificado, no_declarados),
        "sugerencias": sugerencias,
        "detalle": {
            "conciliados": conciliados,
            "falta_certificado": falta_certificado,
            "no_declarados": no_declarados,
        },
    }


def _cuits_distintos(r: dict, m: dict) -> bool:
    return bool(r.get("cuit") and m.get("cuit") and r["cuit"] != m["cuit"])


def _sugerencias(
    arca: list[dict],
    mayor: list[dict],
    libres_arca: list[int],
    libres_mayor: list[int],
    tolerancia: int,
) -> list[dict]:
    """
    Pares que el cruce no hizo solo pero que probablemente sean la misma
    retencion, para que una persona los confirme:

    * el asiento menciona el numero de certificado (aunque el importe difiera);
    * mismo importe, pero la fecha esta fuera de la tolerancia;
    * mismo importe, pero el asiento esta en la cuenta de otro impuesto;
    * mismo agente con una diferencia chica de importe (redondeo, tipeo).

    Cada lado aparece en una sola sugerencia: la mas fuerte.
    """
    posibles: list[tuple[tuple, int, int, str, str]] = []
    for i in libres_arca:
        r = arca[i]
        for j in libres_mayor:
            m = mayor[j]
            dias = _distancia_dias(r["fecha"], m["fecha"])
            diferencia = abs(dec(r["importe"]) - dec(m["importe"]))
            mismo_agente = (r.get("cuit") and r["cuit"] == m.get("cuit")) or (
                _similitud(r["razon_social"], m["razon_social"]) >= SIMILITUD_RAZON_SOCIAL
            )

            if _certificado_en_asiento(r, m):
                fuerza, motivo = "alta", "El asiento menciona el número de certificado"
                if diferencia:
                    motivo += f", con una diferencia de {_pesos(diferencia)}"
                clave = (0, diferencia, dias or 0)
            elif _cuits_distintos(r, m):
                continue
            elif diferencia == 0 and not _impuestos_compatibles(r, m):
                fuerza = "media"
                motivo = f"Mismo importe, pero el asiento está en una cuenta de {m['impuesto']} y ARCA lo informa como {r['impuesto']}"
                clave = (1, 0, dias or 0)
            elif diferencia == 0 and dias is not None and tolerancia < dias <= DIAS_SUGERIBLES:
                fuerza = "alta" if mismo_agente else "media"
                motivo = f"Mismo importe, con {dias} días entre la retención y el asiento"
                clave = (1 if mismo_agente else 2, 0, dias)
            elif (
                mismo_agente
                and 0 < diferencia <= max(DIFERENCIA_SUGERIBLE_ABS, dec(r["importe"]) * DIFERENCIA_SUGERIBLE_REL)
                and (dias is None or dias <= DIAS_SUGERIBLES)
                and _impuestos_compatibles(r, m)
            ):
                fuerza = "media"
                motivo = f"Mismo agente, con una diferencia de {_pesos(diferencia)}"
                clave = (2, diferencia, dias or 0)
            else:
                continue
            posibles.append((clave, i, j, fuerza, motivo))

    sugerencias = []
    vistos_arca: set[int] = set()
    vistos_mayor: set[int] = set()
    for _, i, j, fuerza, motivo in sorted(posibles, key=lambda p: p[0]):
        if i in vistos_arca or j in vistos_mayor:
            continue
        vistos_arca.add(i)
        vistos_mayor.add(j)
        r, m = arca[i], mayor[j]
        sugerencias.append(
            {
                "id_arca": id_arca(i),
                "id_mayor": id_mayor(j),
                "fuerza": fuerza,
                "motivo": motivo,
                "arca": _a_salida(r, certificado=r.get("certificado")),
                "mayor": _a_salida(m, documento=m.get("documento"), cuenta=m.get("cuenta")),
                "diferencia_importe": float(dec(r["importe"]) - dec(m["importe"])),
                "diferencia_dias": _distancia_dias(r["fecha"], m["fecha"]),
            }
        )
    return sugerencias


def _por_impuesto(conciliados: list[dict], falta: list[dict], no_declarados: list[dict]) -> list[dict]:
    """
    Por impuesto, lo que informa ARCA, lo registrado en el mayor y la
    diferencia: es lo que se controla antes de computar las retenciones en la
    DDJJ de cada impuesto.
    """
    filas: dict[str, dict] = {}

    def fila(impuesto: str) -> dict:
        nombre = impuesto or "Sin identificar"
        return filas.setdefault(
            nombre,
            {
                "impuesto": nombre,
                "cantidad_arca": 0,
                "cantidad_mayor": 0,
                "importe_arca": Decimal("0"),
                "importe_mayor": Decimal("0"),
                "conciliado": Decimal("0"),
                "falta_certificado": Decimal("0"),
                "no_declarado": Decimal("0"),
            },
        )

    for c in conciliados:
        f = fila(c["impuesto"])
        f["cantidad_arca"] += 1
        f["cantidad_mayor"] += 1
        f["importe_arca"] += dec(c["importe"])
        f["importe_mayor"] += dec(c["importe_mayor"])
        f["conciliado"] += dec(c["importe"])
    for m in falta:
        f = fila(m["impuesto"])
        f["cantidad_mayor"] += 1
        f["importe_mayor"] += dec(m["importe"])
        f["falta_certificado"] += dec(m["importe"])
    for r in no_declarados:
        f = fila(r["impuesto"])
        f["cantidad_arca"] += 1
        f["importe_arca"] += dec(r["importe"])
        f["no_declarado"] += dec(r["importe"])

    salida = []
    for f in sorted(filas.values(), key=lambda f: -f["importe_arca"]):
        diferencia = f["importe_arca"] - f["importe_mayor"]
        salida.append(
            {
                **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in f.items()},
                "diferencia": float(diferencia),
            }
        )
    return salida
