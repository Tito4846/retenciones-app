import logging
from contextlib import asynccontextmanager

import sqlite3

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.conexiones import router as router_conexiones
from app.api.lotes import router as router_lotes
from app.api.rutas import router
from app.core.config import settings
from app.core.usuarios import Usuario, usuario_actual
from app.services.lotes import ErrorDeLote

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    yield
    # Al apagar el servidor se cierran las ventanas de los bots.
    from app.bots import conexiones

    conexiones.arca.desconectar()
    conexiones.xubio.desconectar()


app = FastAPI(title="Conciliación de Retenciones ARCA", lifespan=ciclo_de_vida)


class ErrorInterno:
    """
    Un error sin manejar responde con su mensaje. Va adentro de CORS: si no, la
    respuesta sale sin sus encabezados, el navegador la esconde y la pantalla
    dice que no hay conexión con el servidor.
    """

    def __init__(self, siguiente: ASGIApp) -> None:
        self.siguiente = siguiente

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.siguiente(scope, receive, send)
            return
        empezada = False

        async def enviar(mensaje: Message) -> None:
            nonlocal empezada
            empezada = empezada or mensaje["type"] == "http.response.start"
            await send(mensaje)

        try:
            await self.siguiente(scope, receive, enviar)
        except Exception:
            logger.exception("Error interno en %s %s", scope["method"], scope["path"])
            if empezada:
                raise
            cuerpo = {"detail": {"codigo": "ERROR_INTERNO", "mensaje": "Algo falló en el servidor. Probá de nuevo y, si sigue, avisá a sistemas.", "errores": []}}
            await JSONResponse(cuerpo, status_code=500)(scope, receive, send)


app.add_middleware(ErrorInterno)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # ng serve usa 4201, 4202... cuando el puerto pedido esta ocupado: cualquier
    # puerto de localhost sirve igual.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Usuario-Id", "X-Usuario-Nombre", "X-Usuario-Roles"],
    # Sin esto el navegador le esconde al front el nombre del archivo exportado.
    expose_headers=["Content-Disposition", "Content-Length"],
)

# Todo pide un usuario con rol salvo /api/salud. Lotes lo pide ruta por ruta.
con_sesion = [Depends(usuario_actual)]
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


@app.get("/api/yo")
def yo(usuario: Usuario = Depends(usuario_actual)) -> dict:
    """Quién es y qué roles tiene: el front arma el menú y los permisos con esto."""
    return {"id": usuario.id, "nombre": usuario.nombre, "roles": sorted(usuario.roles)}


def _servir_front(carpeta: Path) -> None:
    """
    El build del front (todo se arma en el navegador) servido en el mismo dominio
    que la API, sin CORS. Los
    archivos que existen se devuelven tal cual; cualquier otra ruta es de Angular.
    """
    raiz = carpeta.resolve()
    indice = next((raiz / n for n in ("index.csr.html", "index.html") if (raiz / n).is_file()), None)
    if indice is None:
        logger.warning("FRONT_DIST=%s no tiene index.html: se sirve solo la API.", raiz)
        return

    @app.get("/{ruta:path}", include_in_schema=False)
    def front(ruta: str) -> FileResponse:
        if ruta == "api" or ruta.startswith("api/"):
            raise HTTPException(404)
        archivo = (raiz / ruta).resolve()
        if ruta and archivo.is_file() and archivo.is_relative_to(raiz):
            # Los nombres llevan hash: se pueden guardar en caché para siempre.
            return FileResponse(archivo, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        return FileResponse(indice, headers={"Cache-Control": "no-cache"})

    logger.info("Sirviendo el front desde %s", raiz)


if settings.FRONT_DIST and Path(settings.FRONT_DIST).is_dir():
    _servir_front(Path(settings.FRONT_DIST))
