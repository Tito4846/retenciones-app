# Conciliación de Retenciones ARCA

Aplicación para conciliar las retenciones y percepciones sufridas que figuran en
**ARCA (Mis Retenciones)** contra el **Libro Mayor de Xubio**, y exportar el
resultado como papel de trabajo en Excel.

- **backend/** — API en FastAPI (Python). Importa archivos, concilia, exporta a
  Excel y maneja los bots de Chrome (Playwright) que se conectan a ARCA y Xubio.
- **frontend/** — Angular 22 con SSR y Tailwind CSS.

## Requisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | reciente | Maneja Python y las dependencias del backend |
| Python | 3.14 (lo descarga `uv` solo, ver `backend/.python-version`) | Backend |
| Node.js | 22 o superior (probado con 24) | Frontend |
| npm | 11 | Frontend |
| Google Chrome | instalado | Los bots usan el Chrome del sistema (no hace falta `playwright install`) |

## Instalación

```bash
cd backend
uv sync
```

```bash
cd frontend
npm ci
```

> Ejecutá `uv sync` desde una terminal normal (PowerShell, CMD o la de VS Code).
> Si se ejecuta desde una app empaquetada de Windows (por ejemplo, el desktop de
> Claude), Windows guarda el Python descargado en una carpeta virtualizada de
> esa app y después falla con *"did not find executable at
> ...\uv\python\cpython-3.14...\python.exe"* o *"Missing expected target
> directory for Python minor version link"*. Para arreglarlo, borrá
> `backend/.venv` y volvé a ejecutar `uv sync` desde tu terminal.

### Configuración (opcional)

El backend funciona sin configuración. Para cambiar valores, copiá el ejemplo y
editalo:

```bash
cp backend/.env.example backend/.env
```

Variables principales:

| Variable | Default | Descripción |
|---|---|---|
| `TOLERANCIA_DIAS` | `5` | Días de diferencia aceptados entre la fecha de ARCA y la del asiento |
| `EMPRESA` | vacío | Nombre que aparece en el membrete del Excel |
| `HEADLESS` | `false` | Chrome visible (necesario para el doble factor de ARCA y el captcha de Xubio) |
| `ARCA_SERVICIO` | `Mis Retenciones` | Nombre del servicio en el buscador del portal de ARCA |
| `ARCA_RENOVAR_SESION_MIN` | `50` | La sesión de ARCA se renueva sola con la misma CUIT y clave (solo en memoria mientras la ventana está abierta) cuando vence y, antes, a estos minutos sin uso. `0` = solo cuando vence. Con doble factor solo se renueva al vencer |
| `XUBIO_USUARIO` / `XUBIO_CLAVE` | vacío | Si se completan, el bot escribe el ingreso a Xubio solo |
| `XUBIO_CUENTAS_RETENCIONES` | `retencion,retenciones,percepcion,percepciones` | Palabras que identifican las cuentas de retenciones |
| `CORS_ORIGINS` | `[]` | Orígenes extra permitidos (cualquier `localhost` ya está permitido) |

La lista completa está en `backend/app/core/config.py`. La URL de la API que usa
el frontend está en `frontend/src/environments/environment.ts`
(`http://localhost:8000/api`).

## Levantar en desarrollo

Desde la raíz del proyecto, en dos terminales:

**Backend** (http://localhost:8000, docs en http://localhost:8000/docs):

```bash
uv run --directory backend uvicorn app.main:app --port 8000 --reload --reload-dir app
```

**Frontend** (http://localhost:4200):

```bash
npm --prefix frontend start
```

Abrí http://localhost:4200 en el navegador. Para comprobar que el backend
responde: http://localhost:8000/api/salud devuelve `{"status":"ok"}`.

## Cómo concilia

Cada conciliación es **un mes cerrado, una empresa (CUIT) y un impuesto**, de los dos lados:

1. Mes y empresa: del 1 al último día del mes, con la CUIT del retenido.
2. ARCA: Mis Retenciones filtrado por esa CUIT, el impuesto y el mes.
3. Xubio: con la cuenta con la que se entró en Conexiones, el bot hace el recorrido de
   una persona: entra a Contabilidad → Libro Mayor, en «Elige los
   filtros» pone las mismas fechas que ARCA y en «Cuenta» escribe el impuesto y elige,
   entre las cuentas del plan de la empresa, la del tipo consultado («Retenciones de
   Ganancias sufridas», «Percepciones de IVA sufridas»…; con «Retención y/o Percepción»,
   las dos). Toca Aceptar y se queda con lo que carga Xubio. Si el menú no lleva a Libro
   Mayor, poné su dirección en `XUBIO_URL_LIBRO_MAYOR` del `.env`; si la pantalla falla,
   queda una captura en `backend/datos/debug` y el bot sigue con el reporte del mayor.
4. Cruce comprobante contra comprobante. Si los dos archivos no son del mismo mes o
   impuesto, el resultado (y el Excel) lo avisan arriba de todo.

El cruce en sí:

1. **Normalización**: fechas a un solo formato, CUIT sin guiones, importes numéricos
   (`1.234,56` y `1,234.56` valen lo mismo) y comprobantes sin guiones ni ceros a la
   izquierda (`0001-00012345` → `100012345`). Ver `backend/app/services/normalizacion.py`.
2. **Cruce** uno a uno y con el importe exacto al centavo:
   - clave principal: número de comprobante (el certificado de ARCA en el documento o
     la descripción del asiento);
   - clave secundaria: CUIT del agente de retención + importe;
   - si el asiento no tiene CUIT: razón social parecida, o importe + fecha dentro de
     `TOLERANCIA_DIAS`.
   - mismo agente y fecha cercana con hasta $1 de diferencia (un redondeo al cargar el
     asiento): se concilia y la diferencia queda a la vista;
   - una percepción no va contra la cuenta de retenciones (ni al revés).

   No entran al cruce el saldo inicial, el asiento de apertura del ejercicio, las
   aplicaciones a la DDJJ (Haber) ni las filas sin fecha (totales o anotaciones a mano).
3. **Clasificación**:
   - **A · Conciliados**: coinciden en los dos lados.
   - **B · Faltan en ARCA**: están en el Libro Mayor de Xubio pero ARCA no las informa (reclamo al agente).
   - **C · Faltan en el Mayor**: ARCA las informa y no están registradas (ajuste contable).

El resultado muestra cantidad y monto de cada categoría y los primeros 10 casos de B y C.
El Excel trae dos pestañas con las planillas originales, lo conciliado pintado de amarillo:
`ARCA` (el export de Mis Retenciones, con el total al pie) y `LIBRO MAYOR` (el mayor de Xubio
con el saldo corrido). Debajo del mayor van dos apartados con su título: **Partidas de ARCA
pendientes de registración** (lo que falta en el mayor, importe en el Debe) y **Partidas
contabilizadas - revisar procedencia / revisar cliente** (lo que falta en ARCA, el mismo asiento
con el importe en el Haber). Al pie, el control: **saldo ARCA** (el total de la hoja ARCA),
**saldo Xubio** (el saldo corrido después de los dos apartados) y **dif**, que debería dar 0.
Si no da 0, la dif queda en rojo y debajo van, también en rojo, los motivos con lo que suma cada
uno (redondeos en conciliadas, saldo inicial o apertura, aplicaciones del Haber). En la pantalla, el cuadro «Diferencia ARCA − Mayor» hace lo mismo: lista
los motivos y marca en rojo las filas responsables en el detalle.

## Tests

```bash
cd backend
uv run pytest
```

```bash
cd frontend
npm test
```

## Build de producción del frontend

```bash
cd frontend
npm run build
npm run serve:ssr:retenciones-frontend
```

## Estructura

```
backend/
  app/
    api/        Rutas de la API (conciliación y conexiones de los bots)
    bots/       Automatización de Chrome para ARCA y Xubio
    core/       Configuración (variables de entorno)
    services/   Importadores, conciliador, exportador a Excel
    assets/     Logo del membrete
  datos/        Descargas de los bots y perfil de Chrome (se crea solo, fuera de git)
  tests/
frontend/
  src/app/
    core/       Modelos, servicios y stores
    features/   Pantallas: importar, consulta ARCA, conciliación, conexiones
```
