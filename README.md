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

## Revisión (Maker-Checker)

El diseño completo está en [docs/diseno-revision.md](docs/diseno-revision.md). En resumen:

- Desde **Conciliación**, el empleado toca «Generar Lote para Revisión» y confirma (cliente, CUIT y período salen del cruce; con archivos subidos a mano se escribe solo lo que falta): se genera la v1 del lote con
  cada partida clasificada (Conciliado Automático, Diferencia de Monto, Pendiente de Comprobante,
  No Localizado en ERP / en Fisco) y un código de discrepancia. El Excel suma las hojas
  `CONCILIACION` (protegida: solo se editan Justificación y Comentario) y `_META` (oculta).
- **Revisión** es una bandeja: los lotes a la izquierda y, a la derecha, solo las partidas que no
  conciliaron solas. Cada excepción necesita tipo de justificación, texto y archivo de respaldo.
  La diferencia neta (ARCA contra Libro Mayor) tiene que quedar en $ 0,00 para enviar y para aprobar:
  se resuelve cargando un **importe ajustado** o quitando la fila con un motivo. Los importes de las
  fuentes no se tocan: el ajuste va aparte y una fila quitada sigue registrada y se puede restaurar.
- El empleado también puede bajar el Excel, trabajarlo y subirlo como versión nueva: pasa por una validación de 6 etapas
  (archivo, estructura, sintaxis, integridad de columnas bloqueadas, reglas, cuadre al centavo).
- Al enviarlo, la versión queda sellada. Un supervisor que **no** lo haya preparado lo toma,
  tilda los controles y lo devuelve (con motivo obligatorio: vuelve a quien lo preparó) o lo aprueba.
  Mientras lo revisa puede corregirlo sin devolverlo: eso arma una versión nueva, la anterior queda
  sellada y la tarjeta muestra «Editado por». Quien preparó un lote nunca lo aprueba.
- **Aprobado es definitivo**: la base (triggers de SQLite) no deja cambiar nada del lote y se habilita
  la exportación fiscal (libro CSV y TXT para la DDJJ). Un error posterior se corrige con un lote rectificativo.
- Todo queda en una auditoría que no se puede editar ni borrar, encadenada por hash
  (`GET /api/auditoria/verificar`, solo supervisor), con hora UTC, usuario e IP.

Los datos viven en `backend/datos/retenciones.db` y los archivos en `backend/datos/archivos/`
(guardados por hash). La bandeja anterior (`datos/revisiones/indice.json`) se migra sola la primera vez.

**Roles:** hay dos. El **empleado** prepara los lotes (arma, justifica, adjunta, envía y anula los suyos). El
**supervisor** hace todo eso y además revisa, aprueba o devuelve y se pasa un lote que tiene otro supervisor.
Nadie aprueba un lote que preparó.

## Ingreso y usuarios

Toda la app pide ingresar con correo y contraseña (salvo `/api/salud`).

**Primera vez.** La cuenta principal es `CUENTA_PRINCIPAL` (por defecto `tito.rodriguez@estudiopiccinini.com.ar`), supervisor.
Al arrancar, si todavía no tiene contraseña, el backend muestra en la consola un enlace de un solo uso
(`http://localhost:4200/configurar#…`, vence a las 24 h). Se abre, se elige la contraseña y listo.
El segundo factor (Google Authenticator) es opcional y se activa desde **Mi cuenta**. Si el enlace se perdió:

```bash
cd backend
uv run python -m app.cli enlace-principal
```

**Usuarios.** La cuenta principal da de alta a los demás en **Usuarios** (correo, nombre y rol): le aparece una
contraseña temporal que se ve una sola vez y que la persona cambia al entrar. Desde ahí también resetea
contraseñas y segundos factores, desbloquea, cambia roles y da de baja (las cuentas no se borran: quedan en la
auditoría). Puede habilitar a otro supervisor a gestionar usuarios. Cada uno cambia su contraseña y activa o
desactiva su segundo factor en **Mi cuenta** (opcional; con `DOSFA_SUPERVISORES=true` pasa a ser obligatorio para los supervisores).

**Si la cuenta principal pierde la contraseña o el celular** (y los códigos de recuperación), quien tiene acceso al
servidor la reinicia: borra su contraseña y su segundo factor y muestra un enlace nuevo.

```bash
cd backend
uv run python -m app.cli reiniciar-principal
```

**Cómo está protegido** (`backend/app/core/seguridad.py`, `backend/app/services/cuentas.py`):

- Contraseñas con **Argon2id**; mínimo 10 caracteres, sin el correo ni el nombre, no las más comunes.
- **Token de acceso** JWT firmado (HS256), de 10 minutos, solo en memoria del navegador (nunca en localStorage).
  Cada pedido además consulta la cuenta: dar de baja, cambiar el rol o la contraseña corta el acceso en el acto.
- **Token de renovación** en cookie `HttpOnly`, `Secure`, `SameSite=Strict`, limitada a `/api/auth`; se rota en
  cada uso y, si alguien reusa uno viejo, se cierra toda la sesión. Vence a los 60 minutos sin uso y a las 12 horas.
  `/renovar` y `/salir` exigen además el encabezado `X-Requested-With` y un origen permitido (CSRF).
- **Segundo factor** TOTP (RFC 6238), con el secreto cifrado en la base y sin aceptar dos veces el mismo código;
  10 códigos de recuperación de un solo uso (guardados como HMAC).
- **Intentos:** 5 fallidos seguidos bloquean la cuenta 15 minutos; además, 20 intentos cada 5 minutos por IP.
  El error es siempre «Correo o contraseña incorrectos» (no se puede averiguar qué correos tienen cuenta).
- Todo queda en la **auditoría** encadenada: ingresos, fallos, bloqueos, salidas, altas, reseteos y reusos de token.
- La clave del servidor (`SECRET_KEY`) se genera sola en `backend/datos/.secreto` (fuera de git). Fuera de
  localhost, servilo detrás de HTTPS.

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
