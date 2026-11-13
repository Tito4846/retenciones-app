# Conciliación de Retenciones ARCA

Módulo de **ContadorWeb** que concilia las retenciones y percepciones sufridas que informa
**ARCA (Mis Retenciones)** contra el **Libro Mayor de Xubio**. El resultado sale como papel de
trabajo en Excel y pasa por un circuito de revisión con dos personas (Maker-Checker) antes
de quedar aprobado para la DDJJ.

**Repositorio:** https://github.com/Tito4846/retenciones-app

Este README es para el programador que tenga que tocar el código o subirlo a un servidor.
El diseño detallado de la revisión (estados, hashes, auditoría) está en
[docs/diseno-revision.md](docs/diseno-revision.md).

---

## Índice

1. [Stack](#stack)
2. [Arranque rápido](#arranque-rápido)
3. [Cómo funciona, de punta a punta](#cómo-funciona-de-punta-a-punta)
4. [Estructura del código](#estructura-del-código)
5. [Reglas del cruce](#reglas-del-cruce)
6. [Revisión (Maker-Checker)](#revisión-maker-checker)
7. [Usuarios y sesión (ContadorWeb)](#usuarios-y-sesión-contadorweb)
8. [API](#api)
9. [Datos y persistencia](#datos-y-persistencia)
10. [Configuración](#configuración)
11. [Tests](#tests)
12. [Despliegue](#despliegue)
13. [Cambios típicos](#cambios-típicos)
14. [Problemas conocidos](#problemas-conocidos)

---

## Stack

| Parte | Tecnología |
|---|---|
| Backend | Python 3.14 · FastAPI · Uvicorn · pandas / openpyxl · SQLite · Playwright (bots) |
| Frontend | Angular 22 (standalone, signals) · Tailwind CSS 4 · Vitest |
| Dependencias | [uv](https://docs.astral.sh/uv/) para Python, npm para Node |
| Producción | Una sola imagen Docker: el backend sirve la API en `/api` y el front compilado en `/` |

---

## Arranque rápido

### Requisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | reciente | Instala Python 3.14 y las dependencias del backend |
| Node.js | 22 o más (probado con 24) | Frontend |
| npm | 11 | Frontend |
| Google Chrome | instalado | Los bots usan el Chrome del sistema (no hace falta `playwright install`) |

### Instalar

```bash
git clone https://github.com/Tito4846/retenciones-app.git
```

```bash
cd retenciones-app/backend
uv sync
```

```bash
cd retenciones-app/frontend
npm ci
```

### Levantar en desarrollo

Dos terminales, desde la raíz del repo.

Backend en http://localhost:8001 (documentación interactiva en http://localhost:8001/docs):

```bash
uv run --directory backend uvicorn app.main:app --port 8001 --reload --reload-dir app
```

Frontend en http://localhost:4200:

```bash
npm --prefix frontend start
```

Para comprobar que el backend responde: http://localhost:8001/api/salud devuelve `{"status":"ok"}`.

Sin `RETENCIONES_MODULO_SECRETO` la app arranca en **modo local**: no hace falta ContadorWeb y
el usuario se elige tocando el perfil abajo a la izquierda (ver
[Usuarios y sesión](#usuarios-y-sesión-contadorweb)).

---

## Cómo funciona, de punta a punta

```text
 ┌──────────── Importar ─────────────┐
 │ Bots (Conexiones)  o  archivos    │
 │ ARCA: Mis Retenciones (CSV/XLS)   │
 │ Xubio: Libro Mayor (webreport)    │
 └───────────────┬───────────────────┘
                 ▼
   services/tareas.py  ── tarea en segundo plano (task_id), el front la consulta cada 2 s
                 ▼
   importador_arca / importador_mayor  → normalizacion → conciliador (A/B/C)
                 ▼
   Pantalla Conciliación: revisar, emparejar a mano, corregir, avisar al cliente
                 ▼
   exportador.py → Excel (hojas ARCA y LIBRO MAYOR pintadas)
                 ▼
   services/lotes.py → lote v1 (partidas + Excel con RESUMEN, CONCILIACION, _META)
                 ▼
   Revisión: el empleado justifica/ajusta/adjunta y envía → un supervisor aprueba o devuelve
                 ▼
   Aprobado = inmutable (triggers SQLite) + exportación fiscal (CSV / TXT)
```

Las pantallas del front son cuatro: **Importar**, **Conciliación**, **Solicitudes de revisión**
y **Conexiones** (los bots).

---

## Estructura del código

```text
backend/
  app/
    main.py            Arma FastAPI: CORS, rutas, /api/salud, /api/yo, /api/sesion/entrar
                       y sirve el front compilado si existe FRONT_DIST.
    api/
      rutas.py         Conciliación: subir archivos, estado de la tarea, pares a mano,
                       correcciones y exportar el Excel.
      conexiones.py    Bots: conectar/desconectar ARCA y Xubio, consultar Mis Retenciones,
                       flujo automático (traer los dos lados y conciliar).
      lotes.py         Lotes de revisión: versiones, partidas, adjuntos, transiciones,
                       exportación fiscal, auditoría y notificaciones.
    bots/
      chrome.py        Opciones de lanzamiento de Chrome (contenedor, ventana minimizada).
      navegador_arca.py  Hilo que maneja la ventana de ARCA: login, doble factor,
                       renovación de sesión, mantener vivo el portal.
      misret.py        Completa el formulario de Mis Retenciones y baja la exportación.
      navegador_xubio.py Hilo de la ventana de Xubio: ingreso por Visma Connect.
      libro_mayor_xubio.py Recorre Contabilidad → Libro Mayor y pide las cuentas del impuesto.
      conexiones.py    Orquesta el flujo automático (Xubio → ARCA → cruce) y sus pasos.
      bitacora.py      Mini-log en memoria que se muestra en la pantalla de Conexiones.
    core/
      config.py        Todas las variables de entorno (pydantic-settings) con su default.
      db.py            SQLite: esquema, triggers de inmutabilidad, migraciones de columnas.
      usuarios.py      Pase de ContadorWeb, tokens de sesión, niveles → roles, usuario_actual().
    services/
      importador_tablas.py  Lee CSV/XLS/XLSX y encuentra el encabezado aunque venga con títulos.
      importador_arca.py    Export de Mis Retenciones → lista normalizada.
      importador_mayor.py   Libro Mayor de Xubio (archivo o webreport XML) → lista normalizada.
      normalizacion.py      Fechas, CUIT, importes y comprobantes a un solo formato.
      impuestos.py          Códigos de ARCA ↔ nombre del impuesto; detectar impuesto en texto.
      cuentas_xubio.py      Qué cuentas del plan de Xubio son de retenciones sufridas.
      conciliador.py        El cruce y la clasificación A/B/C. Es el corazón del módulo.
      exportador.py         Papel de trabajo en Excel (hojas ARCA y LIBRO MAYOR + control).
      tareas.py             Tareas en memoria (task_id): estado, cancelar, rehacer el cruce.
      partidas.py           Convierte el resultado del cruce en partidas del lote, con su código.
      excel_lote.py         Excel del lote (RESUMEN, CONCILIACION protegida, _META oculta).
      validacion_excel.py   Valida el Excel que vuelve a subir el empleado.
      lotes.py              Ciclo de vida del lote: permisos, compuertas, versiones, aprobar.
      auditoria.py          Auditoría append-only encadenada por hash.
      migracion.py          Importa la bandeja anterior (JSON) la primera vez.
      tools.py              Utilidades de lectura (fechas de Excel, nan, cancelación).
    assets/logos/      Logo del membrete del Excel.
  datos/               Base, archivos, descargas de los bots y perfiles de Chrome (fuera de git).
  tests/               pytest (355 tests). Usan una base temporal: no tocan datos/.

frontend/src/app/
  app.ts / app.html    Layout: menú lateral plegable y perfil de usuario.
  app.routes.ts        Rutas: /importar, /conciliacion, /revision, /conexiones.
  app.config.ts        HttpClient (XHR, para el progreso de subida) e inicio de sesión.
  core/
    models/            Tipos de lo que devuelve la API (conciliación, conexiones, lotes).
    services/          Acceso a la API y estado compartido con signals:
      sesion.service.ts      Quién usa la app (pase de ContadorWeb o usuario de prueba).
      conciliacion.service.ts / conciliacion.store.ts  Cruce en curso y su resultado.
      conexiones.store.ts    Estado de los bots (se consulta cada 2 s).
      lotes.store.ts         Bandeja de lotes, lote abierto y todas sus acciones.
    ui/                Piezas reutilizables: confirmaciones, alertas, control segmentado,
                       popconfirm, plegable, perfil de usuario.
    cuit.pipe.ts       Formato 20-12345678-9.
  features/
    importar/          Consulta automática con los bots o subida manual de archivos.
    conciliacion/      Resultado del cruce: tarjetas A/B/C, tabla editable con filtros
                       tipo Excel, emparejar a mano, aviso al cliente, generar el lote.
    revision/          Bandeja de lotes y el lote abierto (tabla de excepciones, totales,
                       acciones del circuito). Las reglas de colores y trabas están en
                       revision.reglas.ts (con tests).
    conexiones/        Tarjetas de conexión de ARCA y Xubio.
  environments/        URL de la API: localhost:8001 en desarrollo, /api en producción.

Dockerfile             Imagen única (front compilado + backend + Chrome + Xvfb).
render.yaml            Blueprint de Render.
docs/                  Diseño del módulo de revisión.
```

---

## Reglas del cruce

Cada conciliación es **un período, una empresa (CUIT) y un impuesto**, de los dos lados.

1. **Normalización** (`normalizacion.py`): fechas a un formato, CUIT sin guiones, importes
   numéricos (`1.234,56` y `1,234.56` valen lo mismo) y comprobantes sin guiones ni ceros a la
   izquierda (`0001-00012345` → `100012345`).
2. **Cruce** (`conciliador.py`), uno a uno y por orden de confianza:
   1. lo emparejado a mano por la persona;
   2. número de comprobante (certificado de ARCA contra documento o descripción del asiento);
   3. CUIT del agente + importe exacto (la fecha desempata);
   4. asiento sin CUIT: razón social parecida, o importe + fecha dentro de `TOLERANCIA_DIAS`;
   5. mismo agente y fecha cercana con hasta $ 1 de diferencia (redondeo): se concilia y la
      diferencia queda a la vista.

   Una percepción nunca va contra la cuenta de retenciones (ni al revés). No entran al cruce el
   saldo inicial, el asiento de apertura, las aplicaciones a la DDJJ (Haber) ni filas sin fecha.
3. **Clasificación**:
   - **A · Conciliados**: están en los dos lados.
   - **B · Faltan en ARCA**: están en el Mayor y ARCA no las informa (reclamo al agente).
   - **C · Faltan en el Mayor**: ARCA las informa y no están registradas (ajuste contable).

El Excel (`exportador.py`) copia las planillas originales en las hojas `ARCA` y `LIBRO MAYOR`,
pinta lo conciliado y agrega debajo del mayor los dos apartados de faltantes y el control
**saldo ARCA − saldo Xubio = dif**. Si la dif no da 0, la lista de motivos sale en rojo.

---

## Revisión (Maker-Checker)

- Al terminar el cruce el lote se arma solo (`/lotes/desde-cruce`) y se abre en **Solicitudes de
  revisión**. Con archivos subidos a mano se genera desde Conciliación con «Generar Lote para
  Revisión».
- Cada partida que no concilió sola es una **excepción**. Se resuelve en su fila: **Ajustar**
  (justificación, comentario e importe ajustado), **Descartar** (con motivo; deja de sumar pero
  queda registrada) o soltar un **comprobante** (PDF/imagen) sobre la fila. Para cambios en
  bloque se baja el Excel, se edita y se sube como versión nueva (`validacion_excel.py` dice qué
  fila corregir si está mal).
- Para enviar y para aprobar, la **diferencia neta** tiene que dar $ 0,00. Los importes de las
  fuentes nunca se tocan: el ajuste va aparte.
- Estados: `borrador → pendiente → en_revision → aprobado`, con `rechazado` (vuelve a quien
  preparó, con motivo obligatorio) y `anulado`. **Quien preparó un lote nunca lo aprueba.**
- **Aprobado es definitivo**: los triggers de SQLite no dejan cambiar nada del lote. Un error
  posterior se corrige con un lote rectificativo. Se habilita la exportación fiscal (CSV y TXT).
- Todo queda en la auditoría encadenada por hash (`GET /api/auditoria/verificar`).

---

## Usuarios y sesión (ContadorWeb)

El módulo **no tiene login propio**. La persona entra a ContadorWeb y abre Retenciones desde el
menú. Los permisos salen de los niveles de acceso de ContadorWeb (`core/usuarios.py`):

| Nivel en ContadorWeb | Rol acá |
|---|---|
| 200 Administrador General · 100 Administrador · niveles con Configuración o todos los módulos | **supervisor** |
| Nivel con el módulo `Impuestos/Retenciones - Supervisión` | **supervisor** |
| Nivel con el módulo `Impuestos/Retenciones` | **empleado** |
| Cualquier otro | sin acceso (403) |

**Entrada:** ContadorWeb firma un *pase* de un solo uso y abre `https://<retenciones>/?pase=<pase>`.
El front lo canjea en `POST /api/sesion/entrar` por un token (dura `SESION_HORAS`) que viaja en
`Authorization: Bearer`. `GET /api/yo` devuelve el usuario y sus roles.

El pase usa el mismo formato que `firmar_pase` de ContadorWeb, firmado con
`RETENCIONES_MODULO_SECRETO` (el **mismo valor** en los dos servicios, mínimo 32 caracteres):

```text
base64url(JSON) + "." + base64url(HMAC-SHA256(RETENCIONES_MODULO_SECRETO, ese texto))
JSON = {"u", "n", "exp", "id"} + {"nivel", "nivel_nombre", "config", "modulos", "general", "estudio", "estudio_nombre"}
```

Vence a los pocos minutos (más de 10 no se acepta) y no se puede usar dos veces.

**Modo local** (sin `RETENCIONES_MODULO_SECRETO`): cada pedido manda `X-Usuario-Id`, `-Nombre`,
`-Nivel` y `-Modulos`, y el usuario de prueba se cambia desde el menú del perfil. Con el secreto
configurado esos encabezados se ignoran. **En un servidor público el secreto es obligatorio.**

---

## API

Todo bajo `/api`. La documentación interactiva completa está en `/docs` con el backend
levantado.

| Grupo | Rutas principales |
|---|---|
| Sesión | `GET /salud` · `GET /yo` · `POST /sesion/entrar` |
| Conciliación | `POST /conciliar` (archivos) · `GET /estado/{task_id}` · `POST /cancelar/{task_id}` · `POST/DELETE /conciliacion/{task_id}/pares` · `POST /conciliacion/{task_id}/movimientos` · `GET /exportar/{task_id}` |
| Bots | `GET /conexiones` · `POST/DELETE /conexiones/arca` · `POST /conexiones/arca/consultar` · `POST /conexiones/arca/cancelar` · `POST/DELETE /conexiones/xubio` · `POST /conexiones/automatico` |
| Lotes | `GET/POST /lotes` · `POST /lotes/desde-cruce` · `GET /lotes/catalogo` · `GET /lotes/{id}` · versiones, `PATCH /lotes/{id}/partidas/{clave}`, adjuntos · `enviar`, `tomar`, `rechazar`, `aprobar`, `anular`, `rectificar` · exportaciones |
| Auditoría | `GET /auditoria` · `GET /auditoria/verificar` · `GET /lotes/{id}/auditoria` · notificaciones |

Los errores de lotes vuelven con `{"detail": {"codigo", "mensaje", "errores": [...]}}`.

---

## Datos y persistencia

Todo vive en `DIRECTORIO_DATOS` (por defecto `backend/datos/`, en Docker `/var/data`):

| Qué | Dónde |
|---|---|
| Base SQLite (lotes, versiones, partidas, auditoría, sesiones) | `retenciones.db` |
| Excel de versiones y comprobantes, guardados por hash | `archivos/` |
| Descargas de los bots y perfil de Chrome de Xubio | `descargas/`, `xubio_perfil/` |
| Capturas cuando una pantalla de Xubio falla | `debug/` |

Las tareas de conciliación (`task_id`) viven **en memoria** y vencen solas: si se reinicia el
servidor se pierden las conciliaciones abiertas, no los lotes. La base no necesita servidor;
las columnas nuevas se agregan solas al arrancar (`core/db.py`).

**Backup:** alcanza con copiar `DIRECTORIO_DATOS` entero con el servicio parado (o
`sqlite3 retenciones.db ".backup copia.db"` en caliente).

---

## Configuración

Todas las variables están en `backend/app/core/config.py` con su default. Para desarrollo:

```bash
cp backend/.env.example backend/.env
```

| Variable | Default | Para qué |
|---|---|---|
| `RETENCIONES_MODULO_SECRETO` | vacío | Secreto compartido con ContadorWeb. Vacío = modo local. **Obligatorio en producción** |
| `SESION_HORAS` | `12` | Duración del token de sesión |
| `EMPRESA` | vacío | Nombre del membrete del Excel |
| `TOLERANCIA_DIAS` | `5` | Días aceptados entre la fecha de ARCA y la del asiento |
| `PERMITIR_ELIMINAR_CERRADOS` | `true` | Deja borrar lotes cerrados (para probar). **`false` con datos reales** |
| `DIRECTORIO_DATOS` | `backend/datos` | Base, archivos y descargas |
| `FRONT_DIST` | vacío | Carpeta del front compilado: si existe, el backend lo sirve |
| `MAX_MB_ARCHIVO` | `20` | Tamaño máximo por archivo subido |
| `CORS_ORIGINS` | `[]` | Orígenes extra (localhost ya está permitido). No hace falta si front y API van juntos |
| `HEADLESS` | `false` | Chrome visible: necesario para el doble factor de ARCA y el captcha de Xubio |
| `MINIMIZAR_VENTANA` | `true` | Minimiza la ventana del bot una vez conectado |
| `ARCA_RENOVAR_SESION_MIN` | `50` | Renueva la sesión de ARCA a estos minutos sin uso (0 = solo al vencer) |
| `ARCA_MANTENER_PORTAL_MIN` | `9` | Cada cuánto se le avisa al portal de ARCA que la sesión sigue en uso |
| `XUBIO_USUARIO` / `XUBIO_CLAVE` | vacío | Cuenta del estudio: el bot escribe el ingreso solo |
| `XUBIO_CUENTAS_RETENCIONES` | `retencion,…` | Palabras que identifican las cuentas de retenciones |
| `XUBIO_URL_LIBRO_MAYOR` | vacío | Dirección del Libro Mayor si el bot no la encuentra en el menú |

Las claves de ARCA y Xubio que se escriben en la pantalla de Conexiones **no se guardan**: viven
en memoria mientras la ventana del bot está abierta.

En el front, la URL de la API está en `frontend/src/environments/` (`http://localhost:8001/api`
en desarrollo, `/api` en producción).

---

## Tests

```bash
cd backend
uv run pytest
```

```bash
cd frontend
npm test
```

Antes de subir algo, también:

```bash
cd frontend
npx ng build
```

---

## Despliegue

En producción es **un solo servicio**: el `Dockerfile` compila el front, instala el backend,
Google Chrome y una pantalla virtual (Xvfb), y el backend sirve todo en el mismo dominio. No hace
falta configurar CORS.

### Render

1. Subir los cambios a GitHub (`main`): con `autoDeploy: true`, Render vuelve a desplegar solo.
2. La primera vez: en Render, **New → Blueprint** y elegir este repo. `render.yaml` crea el
   servicio `retenciones` con un **disco persistente** en `/var/data` (requiere plan pago;
   sin disco se borra todo en cada deploy).
3. En el panel, completar `RETENCIONES_MODULO_SECRETO` (el mismo de ContadorWeb), `EMPRESA` y,
   si se usan, `XUBIO_USUARIO` / `XUBIO_CLAVE`. Agregar `PERMITIR_ELIMINAR_CERRADOS=false`.
4. El health check es `/api/salud`.

### Cualquier otro servidor (VPS, servidor propio)

Con Docker instalado:

```bash
docker build -t retenciones .
```

```bash
docker run -d --name retenciones --restart unless-stopped -p 8001:8001 -v retenciones-datos:/var/data -e RETENCIONES_MODULO_SECRETO=<secreto> -e PERMITIR_ELIMINAR_CERRADOS=false retenciones
```

- Poner delante un proxy con HTTPS (Nginx, Caddy, Traefik) que apunte a `localhost:8001`.
  El contenedor ya arranca con `--proxy-headers`, así la auditoría guarda la IP real.
- El volumen `retenciones-datos` es lo único que hay que respaldar.
- Para actualizar: `git pull`, `docker build` y volver a crear el contenedor con el mismo volumen.

Sin Docker: compilar el front (`npm run build`), apuntar `FRONT_DIST` a
`frontend/dist/retenciones-frontend/browser` y correr
`uv run uvicorn app.main:app --host 0.0.0.0 --port 8001` dentro de `backend/`.

### Checklist antes de producción

- [ ] `RETENCIONES_MODULO_SECRETO` configurado (sin él, cualquiera puede hacerse pasar por otro usuario).
- [ ] `PERMITIR_ELIMINAR_CERRADOS=false`.
- [ ] Disco o volumen persistente en `/var/data`.
- [ ] HTTPS delante.

### Bots en el servidor

La imagen trae Chrome y Xvfb, pero en un servidor **nadie ve la ventana**: el doble factor de
ARCA y el captcha de Xubio no se pueden completar. Ahí funcionan solo las cuentas sin esos
pasos; si no, se usa la importación de archivos o se corre la app en una PC para los bots.

---

## Cambios típicos

| Quiero… | Dónde |
|---|---|
| Reconocer un impuesto nuevo o un código de ARCA | `services/impuestos.py` (`CODIGOS_ARCA`, `_PALABRAS`) + `tests/test_codigos_arca.py` |
| Cambiar cómo se emparejan ARCA y el Mayor | `services/conciliador.py` + `tests/test_conciliacion.py` |
| Leer un formato nuevo de export | `services/importador_tablas.py` (alias de columnas) y el importador de ese lado |
| Cambiar el Excel del papel de trabajo | `services/exportador.py` + `tests/test_exportador.py` |
| Cambiar el Excel del lote o su validación | `services/excel_lote.py`, `services/validacion_excel.py` |
| Agregar una justificación o un código de discrepancia | `services/partidas.py` (lo lee el front desde `/lotes/catalogo`) |
| Cambiar qué traba enviar o aprobar | Backend: `lotes.compuertas()`. Front: `features/revision/revision.reglas.ts` |
| Agregar una columna a la base | `core/db.py` (lista de columnas agregadas: se migra sola) |
| Arreglar un bot cuando ARCA o Xubio cambian la pantalla | `bots/misret.py`, `bots/navegador_arca.py`, `bots/libro_mayor_xubio.py` (selectores arriba de cada archivo y en `config.py`) |
| Agregar una pantalla | `frontend/src/app/features/<nueva>/` + ruta en `app.routes.ts` + entrada en el menú de `app.html` |

Convenciones: código y mensajes en español; importes en el front en centavos enteros
(`revision.util.ts → centavos`) y en el backend con `Decimal`; componentes standalone con signals.

---

## Problemas conocidos

- **`uv sync` desde una app empaquetada de Windows** (por ejemplo, el escritorio de Claude) puede
  dejar Python en una carpeta virtualizada y fallar con *"did not find executable at
  ...\uv\python\cpython-3.14...\python.exe"*. Solución: borrar `backend/.venv` y correr
  `uv sync` desde PowerShell o la terminal de VS Code.
- **Formato del TXT para la DDJJ**: hoy es `CUIT|FECHA|CERTIFICADO|IMPUESTO|IMPORTE`; falta
  ajustarlo al aplicativo definitivo (`services/lotes.py`, exportación fiscal).
- **Tareas en memoria**: una conciliación abierta se pierde si se reinicia el servidor
  (los lotes no).
