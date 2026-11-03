import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.conexiones import router as router_conexiones
from app.api.rutas import router
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
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
    allow_methods=["*"],
    allow_headers=["*"],
    # Sin esto el navegador le esconde al front el nombre del archivo exportado.
    expose_headers=["Content-Disposition", "Content-Length"],
)

app.include_router(router, prefix="/api")
app.include_router(router_conexiones, prefix="/api")


@app.get("/api/salud")
def salud() -> dict:
    return {"status": "ok"}
