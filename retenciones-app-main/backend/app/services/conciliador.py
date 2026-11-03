"""
Conciliacion de retenciones sufridas: Mis Retenciones de ARCA contra el Libro
Mayor de Xubio.

El flujo tiene tres pasos:

1. Normalizacion (ver `normalizacion.py`): fechas, CUIT sin guiones, importes
   numericos y comprobantes sin guiones ni ceros a la izquierda.

2. Cruce, uno a uno (una retencion no puede quedar con dos asientos ni al
   reves) y con el importe exacto AL CENTAVO. Las claves, en orden:

   a. Clave principal: el numero de comprobante. El certificado de ARCA tiene
      que aparecer en el documento o la descripcion del asiento.
   b. Clave secundaria, si el comprobante no cruza (otro formato, o el asiento
      no lo tiene): CUIT del agente + importe. Entre varios candidatos gana el
      de fecha mas cercana.
   c. Si el asiento no tiene CUIT: razon social parecida o, en ultimo caso, el
      mismo importe con la fecha dentro de TOLERANCIA_DIAS (el asiento se
      carga con la fecha del cobro, no la del certificado).

   El impuesto tiene que ser compatible (una retencion de IVA no se empareja
   con un asiento de Ganancias) y si los dos lados tienen CUIT y no
   coinciden, no hay par.

3. Clasificacion:
   A. conciliados      -> coinciden en ARCA y en el Libro Mayor de Xubio.
   B. faltan_en_arca   -> registradas en el Libro Mayor de Xubio que ARCA no informa
                          (no hay certificado que las respalde: reclamo al agente).
   C. faltan_en_mayor    -> informadas por ARCA sin registrar en el Libro Mayor de Xubio
                          (ajuste contable).
"""

import logging
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from app.core.config import settings
from app.services.normalizacion import claves_comprobante, normalizar_arca, normalizar_mayor
from app.services.tools import dec, formatear_cuit

logger = logging.getLogger(__name__)

SIMILITUD_RAZON_SOCIAL = 0.6
_SUFIJOS_SOCIETARIOS = re.compile(r"\b(S\.?A\.?U?|S\.?R\.?L\.?|S\.?A\.?S\.?|S\.?C\.?A\.?|LTDA|SOCIEDAD ANONIMA)\b\.?")

CRITERIO_COMPROBANTE = "comprobante"
CRITERIO_CUIT_IMPORTE = "cuit_importe"
CRITERIO_RAZON_SOCIAL = "razon_social"
CRITERIO_IMPORTE_FECHA = "importe_fecha"
CRITERIO_DIFERENCIA_MENOR = "diferencia_menor"
CRITERIO_MANUAL = "manual"

# Las tres categorias del reporte, en orden.
CATEGORIAS = (
    ("A", "conciliados", "Conciliados", "Coinciden en ARCA y en el Libro Mayor de Xubio"),
    ("B", "faltan_en_arca", "Faltan en ARCA", "Registradas en el Libro Mayor de Xubio que no figuran en ARCA"),
    ("C", "faltan_en_mayor", "Faltan en el Mayor", "Declaradas en ARCA sin registrar en el Libro Mayor de Xubio"),
)
# Casos de B y C que muestra el reporte (el detalle completo va en el Excel).
CASOS_EN_REPORTE = 10


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
    """Mismo impuesto y mismo tipo: una percepcion no va contra la cuenta de retenciones."""
    def iguales(campo: str) -> bool:
        return not arca.get(campo) or not mayor.get(campo) or arca[campo] == mayor[campo]

    return iguales("impuesto") and iguales("tipo")


def _comprobante_coincide(arca: dict, mayor: dict) -> bool:
    """El comprobante de ARCA (normalizado) escrito en el documento o la descripcion del asiento."""
    claves = claves_comprobante(arca.get("certificado"))
    return bool(claves & mayor.get("claves_comprobante", set()))


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
# Diferencia que se concilia sola con el mismo agente y fecha cercana: un
# redondeo al cargar el asiento ($ 0,60), no otra retencion.
DIFERENCIA_MENOR = Decimal("1.00")
# Mismo importe pero fuera de la tolerancia: hasta cuantos dias se sugiere.
DIAS_SUGERIBLES = 60


def id_arca(i: int) -> str:
    return f"a{i}"


def id_mayor(j: int) -> str:
    return f"m{j}"


def _por_fecha(registros: list[dict], indices) -> list[int]:
    """Los mas viejos primero: asi toman los asientos viejos y no se cruzan en diagonal."""
    return sorted(indices, key=lambda i: (registros[i]["fecha"] is None, registros[i]["fecha"] or date.min))


def _cruzar(
    arca: list[dict], mayor: list[dict], tolerancia: int, pares_manuales: dict[int, int]
) -> dict[int, tuple[int, str]]:
    """Paso 2: {indice ARCA: (indice mayor, criterio)}, clave por clave."""
    indice_mayor: dict[Decimal, list[int]] = {}
    for j, m in enumerate(mayor):
        indice_mayor.setdefault(dec(m["importe"]), []).append(j)

    usados_mayor: set[int] = set()
    pares: dict[int, tuple[int, str]] = {}

    def emparejar(i: int, j: int, criterio: str) -> None:
        usados_mayor.add(j)
        pares[i] = (j, criterio)

    def candidatos(i: int, controlar_cuit: bool = True) -> list[int]:
        r = arca[i]
        return [
            j
            for j in indice_mayor.get(dec(r["importe"]), [])
            if j not in usados_mayor
            and _impuestos_compatibles(r, mayor[j])
            and not (controlar_cuit and _cuits_distintos(r, mayor[j]))
        ]

    def libres() -> list[int]:
        return _por_fecha(arca, (i for i in range(len(arca)) if i not in pares))

    # 0) Lo que la persona ya decidio va antes que todo, aunque no coincida.
    for i, j in pares_manuales.items():
        if 0 <= i < len(arca) and 0 <= j < len(mayor) and i not in pares and j not in usados_mayor:
            emparejar(i, j, CRITERIO_MANUAL)

    # a) Clave principal: numero de comprobante. Es la prueba mas fuerte: vale
    #    aunque el asiento tenga cargada otra CUIT.
    for i in libres():
        j = next((j for j in candidatos(i, controlar_cuit=False) if _comprobante_coincide(arca[i], mayor[j])), None)
        if j is not None:
            emparejar(i, j, CRITERIO_COMPROBANTE)

    # b) Clave secundaria: CUIT del agente + importe; la fecha solo desempata.
    for i in libres():
        r = arca[i]
        if not r["cuit"]:
            continue
        mismos = [j for j in candidatos(i) if mayor[j]["cuit"] == r["cuit"]]
        if mismos:
            emparejar(i, min(mismos, key=lambda j: (_distancia_dias(r["fecha"], mayor[j]["fecha"]) or 0, j)), CRITERIO_CUIT_IMPORTE)

    # c) El asiento sin CUIT: razon social parecida o importe + fecha cercana.
    for i in libres():
        r = arca[i]
        mejor = None
        for j in candidatos(i):
            m = mayor[j]
            distancia = _distancia_dias(r["fecha"], m["fecha"])
            if distancia is None or distancia > tolerancia:
                continue
            if _similitud(r["razon_social"], m["razon_social"]) >= SIMILITUD_RAZON_SOCIAL:
                criterio, prioridad = CRITERIO_RAZON_SOCIAL, 0
            else:
                criterio, prioridad = CRITERIO_IMPORTE_FECHA, 1
            clave = (prioridad, distancia, j)
            if mejor is None or clave < mejor[0]:
                mejor = (clave, j, criterio)
        if mejor is not None:
            emparejar(i, mejor[1], mejor[2])

    # d) Mismo agente y fecha cercana con unos centavos de diferencia (ARCA
    #    informa 18.989,60 y el asiento se cargo 18.989): es la misma
    #    retencion, conciliada con la diferencia a la vista.
    for i in libres():
        r = arca[i]
        mejor = None
        for j, m in enumerate(mayor):
            if j in usados_mayor or not _impuestos_compatibles(r, m) or _cuits_distintos(r, m):
                continue
            diferencia = abs(dec(r["importe"]) - dec(m["importe"]))
            distancia = _distancia_dias(r["fecha"], m["fecha"])
            if not 0 < diferencia <= DIFERENCIA_MENOR or distancia is None or distancia > tolerancia:
                continue
            mismo_cuit = bool(r["cuit"]) and r["cuit"] == m["cuit"]
            if not mismo_cuit and _similitud(r["razon_social"], m["razon_social"]) < SIMILITUD_RAZON_SOCIAL:
                continue
            clave = (diferencia, distancia, j)
            if mejor is None or clave < mejor[0]:
                mejor = (clave, j)
        if mejor is not None:
            emparejar(i, mejor[1], CRITERIO_DIFERENCIA_MENOR)

    return pares


def conciliar_retenciones(
    arca: list[dict],
    mayor: list[dict],
    tolerancia_dias: int | None = None,
    hasta_periodo: date | None = None,
    pares_manuales: dict[int, int] | None = None,
) -> dict:
    """
    `arca`: el padron de ARCA; `mayor`: los asientos del Libro Mayor.

    `hasta_periodo`: ultimo dia del periodo consultado. El mayor se pide unos
    dias mas (la tolerancia) para atrapar los asientos cargados con la fecha
    del cobro; los de esos dias que no se emparejan son retenciones del periodo
    siguiente y no van a "faltan en ARCA".

    `pares_manuales`: {indice ARCA: indice mayor} que la persona emparejo a
    mano. Van antes que todo, aunque el importe o la fecha no coincidan.
    """
    tolerancia = settings.TOLERANCIA_DIAS if tolerancia_dias is None else tolerancia_dias

    # 1) Normalizacion.
    arca = [normalizar_arca(r) for r in arca]
    mayor = [normalizar_mayor(m) for m in mayor]

    # 2) Cruce.
    pares = _cruzar(arca, mayor, tolerancia, pares_manuales or {})
    usados_mayor = {j for j, _ in pares.values()}

    # 3) Clasificacion.
    conciliados = []
    for i in _por_fecha(arca, pares):
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

    faltan_en_mayor = [
        _a_salida(
            r,
            id=id_arca(i),
            certificado=r.get("certificado"),
            comprobante=r.get("comprobante"),
            regimen=r.get("regimen"),
            tipo=r.get("tipo") or "",
            nota=_nota_falta_en_mayor(r),
        )
        for i, r in enumerate(arca)
        if i not in pares
    ]
    ultima_arca = max((r["fecha"] for r in arca if r["fecha"]), default=None)

    def del_periodo_siguiente(m: dict) -> bool:
        return hasta_periodo is not None and m["fecha"] is not None and m["fecha"] > hasta_periodo

    sin_par = [j for j in range(len(mayor)) if j not in usados_mayor]
    del_periodo = [j for j in sin_par if not del_periodo_siguiente(mayor[j])]
    faltan_en_arca = [
        _a_salida(
            mayor[j],
            id=id_mayor(j),
            documento=mayor[j].get("documento"),
            cuenta=mayor[j].get("cuenta"),
            descripcion=mayor[j].get("descripcion"),
            tipo=mayor[j].get("tipo") or "",
            nota=_nota_falta_en_arca(mayor[j], ultima_arca, tolerancia),
        )
        for j in del_periodo
    ]
    fuera_de_periodo = len(sin_par) - len(del_periodo)

    sugerencias = _sugerencias(
        arca, mayor, [i for i in range(len(arca)) if i not in pares], del_periodo, tolerancia
    )

    def total(filas: list[dict], campo: str = "importe") -> float:
        return float(sum((dec(f[campo]) for f in filas), Decimal("0")))

    detalle = {
        "conciliados": conciliados,
        "faltan_en_arca": faltan_en_arca,
        "faltan_en_mayor": faltan_en_mayor,
    }
    importe_arca = total(conciliados) + total(faltan_en_mayor)
    importe_mayor = total(conciliados, "importe_mayor") + total(faltan_en_arca)
    resumen = {
        "total_conciliados": len(conciliados),
        "total_faltan_en_arca": len(faltan_en_arca),
        "total_faltan_en_mayor": len(faltan_en_mayor),
        "total_manuales": sum(1 for c in conciliados if c["criterio"] == CRITERIO_MANUAL),
        "total_sugerencias": len(sugerencias),
        "importe_conciliado": total(conciliados),
        "importe_faltan_en_arca": total(faltan_en_arca),
        "importe_faltan_en_mayor": total(faltan_en_mayor),
        # Lo que dice cada lado y cuanto difieren: es el control de la DDJJ.
        "importe_arca": importe_arca,
        "importe_mayor": importe_mayor,
        "diferencia": float(dec(importe_arca) - dec(importe_mayor)),
        "total_arca": len(arca),
        "total_mayor": len(conciliados) + len(faltan_en_arca),
        "fuera_de_periodo": fuera_de_periodo,
        "tolerancia_dias": tolerancia,
        "hasta_periodo": hasta_periodo.isoformat() if hasta_periodo else None,
        "por_criterio": {
            criterio: sum(1 for c in conciliados if c["criterio"] == criterio)
            for criterio in (CRITERIO_COMPROBANTE, CRITERIO_CUIT_IMPORTE, CRITERIO_RAZON_SOCIAL,
                             CRITERIO_IMPORTE_FECHA, CRITERIO_DIFERENCIA_MENOR, CRITERIO_MANUAL)
        },
    }
    logger.info(
        "Cruce: A %d conciliados (%d a mano), B %d faltan en ARCA, C %d faltan en el mayor, %d sugerencias",
        len(conciliados),
        resumen["total_manuales"],
        len(faltan_en_arca),
        len(faltan_en_mayor),
        len(sugerencias),
    )
    return {
        "resumen": resumen,
        "avisos": controles(arca, mayor, tolerancia, hasta_periodo),
        "categorias": categorias(detalle),
        "por_impuesto": _por_impuesto(conciliados, faltan_en_arca, faltan_en_mayor),
        "sugerencias": sugerencias,
        "detalle": detalle,
    }


def _rango(fechas: list[date]) -> str:
    return f"{min(fechas):%d/%m/%Y} al {max(fechas):%d/%m/%Y}"


def _lista(nombres: set[str]) -> str:
    ordenados = sorted(nombres)
    return ordenados[0] if len(ordenados) == 1 else ", ".join(ordenados[:-1]) + " y " + ordenados[-1]


def controles(arca: list[dict], mayor: list[dict], tolerancia: int, hasta_periodo: date | None = None) -> list[str]:
    """
    Que los dos archivos sean del mismo cruce: la misma empresa, el mismo
    impuesto y el mismo mes cerrado. Si no, el resultado se llena de faltantes
    falsos; cada aviso dice que bajar de nuevo.
    """
    avisos = []
    impuestos_arca = {r["impuesto"] for r in arca if r["impuesto"]}
    impuestos_mayor = {m["impuesto"] for m in mayor if m["impuesto"]}
    if impuestos_arca and impuestos_mayor - impuestos_arca:
        otros = impuestos_mayor - impuestos_arca
        avisos.append(
            f"El Libro Mayor trae asientos de {_lista(otros)} y ARCA solo retenciones de {_lista(impuestos_arca)}: "
            f"bajá el mayor de la cuenta de retenciones de {_lista(impuestos_arca)}. "
            "Mientras tanto esos asientos quedan en «Faltan en ARCA»."
        )
    if impuestos_mayor and impuestos_arca - impuestos_mayor:
        faltantes = impuestos_arca - impuestos_mayor
        avisos.append(
            f"ARCA trae retenciones de {_lista(faltantes)} pero el Libro Mayor no tiene esa cuenta: "
            "revisá que hayas bajado la cuenta correcta. Esas retenciones quedan en «Faltan en el Mayor»."
        )

    fechas_arca = [r["fecha"] for r in arca if r["fecha"]]
    fechas_mayor = [m["fecha"] for m in mayor if m["fecha"] and (hasta_periodo is None or m["fecha"] <= hasta_periodo)]
    if fechas_arca and fechas_mayor:
        primera, ultima = min(fechas_arca), max(fechas_arca)
        # El asiento se carga unos dias despues del certificado: eso no es otro periodo.
        despues = [f for f in fechas_mayor if f > ultima + timedelta(days=tolerancia)]
        antes = [f for f in fechas_mayor if f < primera.replace(day=1)]
        if despues:
            avisos.append(
                f"El Libro Mayor tiene {len(despues)} asiento/s posteriores al {ultima:%d/%m/%Y}, la última "
                "retención que informa ARCA: quedan en «Faltan en ARCA» hasta que los agentes las declaren, "
                "o bajá ARCA hasta la misma fecha que el mayor."
            )
        if antes:
            avisos.append(
                f"El Libro Mayor tiene {len(antes)} asiento/s anteriores al {primera:%m/%Y}, el primer mes que "
                "trae ARCA: bajá los dos desde la misma fecha."
            )
    return avisos


def categorias(detalle: dict) -> list[dict]:
    """El resumen cuantitativo: cantidad de comprobantes y monto total de A, B y C."""
    return [
        {
            "categoria": letra,
            "clave": clave,
            "nombre": nombre,
            "descripcion": descripcion,
            "cantidad": len(detalle[clave]),
            "importe": float(sum((dec(f["importe"]) for f in detalle[clave]), Decimal("0"))),
        }
        for letra, clave, nombre, descripcion in CATEGORIAS
    ]


def _nota_falta_en_mayor(r: dict) -> str | None:
    if r.get("tipo") == "Percepcion":
        return f"Es una percepción: va en la cuenta de percepciones de {r['impuesto'] or 'ese impuesto'}."
    return None


def _nota_falta_en_arca(m: dict, ultima_arca: date | None, tolerancia: int) -> str | None:
    """Lo cobrado despues de lo ultimo que informa ARCA: el agente todavia puede no haberlo declarado."""
    if ultima_arca and m["fecha"] and m["fecha"] > ultima_arca + timedelta(days=tolerancia):
        return (
            f"Posterior a la última retención que informa ARCA ({ultima_arca:%d/%m/%Y}): "
            "puede que el agente todavía no la haya declarado."
        )
    return None


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

            if _comprobante_coincide(r, m):
                fuerza, motivo = "alta", "El asiento menciona el número de comprobante"
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


def _por_impuesto(conciliados: list[dict], falta: list[dict], faltan_en_mayor: list[dict]) -> list[dict]:
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
                "faltan_en_arca": Decimal("0"),
                "faltan_en_mayor": Decimal("0"),
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
        f["faltan_en_arca"] += dec(m["importe"])
    for r in faltan_en_mayor:
        f = fila(r["impuesto"])
        f["cantidad_arca"] += 1
        f["importe_arca"] += dec(r["importe"])
        f["faltan_en_mayor"] += dec(r["importe"])

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
