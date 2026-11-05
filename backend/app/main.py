import logging
from contextlib import asynccontextmanager

import sqlite3

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as router_auth
from app.api.conexiones import router as router_conexiones
from app.api.lotes import router as router_lotes
from app.api.rutas import router
from app.core.config import settings
from app.core.usuarios import usuario_actual
from app.services.lotes import ErrorDeLote

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    # Si la cuenta principal no tiene contraseña, el enlace para elegirla sale en esta consola.
    from app.services import cuentas

    cuentas.anunciar_configuracion()
    yield
    # Al apagar el servidor se cierran las ventanas de los bots.
    from app.bots import conexiones

    conexiones.arca.desconectar()
    conexiones.xubio.desconectar()


app = FastAPI(title="Conciliación de Retenciones ARCA", lifespan=ciclo_de_vida)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # ng serve usa 4201, 4202... cuando el puerto pedido esta ocupado: cualquier
    # puerto de localhost sirve igual.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    # Sin esto el navegador le esconde al front el nombre del archivo exportado.
    expose_headers=["Content-Disposition", "Content-Length"],
)

# Todo pide sesión salvo /api/salud y el ingreso. Lotes y usuarios la piden ruta por ruta.
con_sesion = [Depends(usuario_actual)]
app.include_router(router_auth, prefix="/api")
app.include_router(router, prefix="/api", dependencies=con_sesion)
app.include_router(router_conexiones, prefix="/api", dependencies=con_sesion)
app.include_router(router_lotes, prefix="/api")


@app.middleware("http")
async def encabezados_de_seguridad(request: Request, siguiente):
    respuesta = await siguiente(request)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("X-Frame-Options", "DENY")
    respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
    respuesta.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    if request.url.path.startswith(("/api/auth", "/api/usuarios")):
        # Tokens y contraseñas temporales: que no queden en ningún caché.
        respuesta.headers["Cache-Control"] = "no-store"
        respuesta.headers["Pragma"] = "no-cache"
    return respuesta


@app.exception_handler(ErrorDeLote)
def error_de_lote(_: Request, error: ErrorDeLote) -> JSONResponse:
    return JSONResponse({"detail": error.detalle()}, status_code=error.estado)


@app.exception_handler(sqlite3.IntegrityError)
def bloqueo_de_la_base(_: Request, error: sqlite3.IntegrityError) -> JSONResponse:
    # Los triggers de inmutabilidad: la API ya lo controla antes, esto es la red de seguridad.
    return JSONResponse({"detail": {"codigo": "INMUTABLE", "mensaje": str(error), "errores": []}}, status_code=409)


@app.get("/api/salud")
def salud() -> dict:
    return {"status": "ok"}
