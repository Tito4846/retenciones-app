# Imagen única para Render: el backend (FastAPI) sirve la API y el front ya compilado
# en el mismo dominio. Ver render.yaml y la sección «Despliegue en Render» del README.

# ---------- 1. Front (Angular) ----------
FROM node:24-slim AS front
WORKDIR /front
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 2. Backend (FastAPI) ----------
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project

# Bots: Google Chrome (CANAL_CHROME=chrome) y una pantalla virtual (Xvfb), porque
# ARCA suele bloquear el modo headless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./
COPY --from=front /front/dist/retenciones-frontend/browser /app/front

# DIRECTORIO_DATOS: base SQLite, archivos y clave del servidor. En Render va el disco
# persistente montado ahí (si no, se borra todo en cada deploy).
ENV FRONT_DIST=/app/front \
    DIRECTORIO_DATOS=/var/data \
    MINIMIZAR_VENTANA=false

EXPOSE 8001
# Render pasa el puerto en $PORT. --proxy-headers: la IP real del cliente (auditoría
# y límite de intentos) viene en X-Forwarded-For desde el proxy de Render.
CMD ["sh", "-c", "exec xvfb-run -a uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --proxy-headers --forwarded-allow-ips='*'"]
