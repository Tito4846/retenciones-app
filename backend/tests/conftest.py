import pytest
from openpyxl import Workbook

from app.core.config import settings


@pytest.fixture(autouse=True)
def usuario_por_defecto(monkeypatch):
    """
    El usuario sale de los encabezados X-Usuario-* (como lo va a mandar la página
    principal). Sin ellos, el de la configuración: en los tests, un supervisor «tester».
    """
    monkeypatch.setattr(settings, "USUARIO_POR_DEFECTO_ID", "tester")
    monkeypatch.setattr(settings, "USUARIO_POR_DEFECTO_NOMBRE", "tester")
    monkeypatch.setattr(settings, "USUARIO_POR_DEFECTO_ROLES", "supervisor")

ARCA_CSV = """Mis Retenciones - Período 10/2026
CUIT Agente Ret./Perc.;Denominación;Impuesto;Descripción Impuesto;Régimen;Fecha Ret./Perc.;Número Certificado;Importe Ret./Perc.
30-50000000-1;BANCO EJEMPLO SA;767;IVA;Retenciones IVA;04/10/2026;0001-00012345;32.110,00
20-12345678-9;JUAN PEREZ;217;Ganancias;Ganancias - Servicios;10/10/2026;55555;16.100,00
30-71234567-8;PROVEEDOR SRL;767;IVA;Retenciones IVA;20/10/2026;77777;8.300,75
30-71234567-8;PROVEEDOR SRL;217;Ganancias;Ganancias - Bienes;20/10/2026;88888;1.500,00
"""

MAYOR_FILAS = [
    # Fecha, Documento, Cuenta, Debe, Haber, Descripción, Organización
    ("01/10/2026", "", "Retenciones IVA sufridas", 0, 0, "Saldo Inicial", ""),
    # Par por certificado aunque la fecha este a 12 dias.
    ("16/10/2026", "REC 0001-00004521", "Retenciones IVA sufridas", 32110.00, 0, "Cobro Cert. 12345", "Banco Ejemplo S.A."),
    # Par por razon social e importe.
    ("11/10/2026", "REC 0001-00004522", "Retenciones Ganancias sufridas", 16100.00, 0, "Cobro factura 33", "Pérez Juan"),
    # Mismo importe que la de Ganancias de PROVEEDOR pero es IVA: no empareja.
    ("21/10/2026", "REC 0001-00004530", "Retenciones IVA sufridas", 1500.00, 0, "Cobro", "Proveedor SRL"),
    # Aplicacion a la DDJJ: va al Haber y no participa.
    ("31/10/2026", "AS 99", "Retenciones IVA sufridas", 0, 40000.00, "Aplicación DDJJ IVA", ""),
]


@pytest.fixture
def ruta_arca(tmp_path):
    ruta = tmp_path / "mis_retenciones.csv"
    ruta.write_text(ARCA_CSV, encoding="cp1252")
    return str(ruta)


@pytest.fixture
def ruta_mayor(tmp_path):
    libro = Workbook()
    hoja = libro.active
    hoja.append(["Libro Mayor - Xubio"])
    hoja.append(["Período: 01/10/2026 al 31/10/2026"])
    hoja.append([])
    hoja.append(["Fecha", "Documento", "Cuenta", "Debe", "Haber", "Descripción", "Organización"])
    for fila in MAYOR_FILAS:
        hoja.append(list(fila))
    ruta = tmp_path / "mayor.xlsx"
    libro.save(ruta)
    return str(ruta)
