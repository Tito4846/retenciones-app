"""
Lo que fue haciendo cada bot, para el mini-log de «Conexiones».

Solo en memoria y sin datos sensibles: los mensajes son los mismos que ya se
muestran en la pantalla (nunca la clave). No es thread-safe por sí sola: el
navegador la usa dentro de su propio candado.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Literal

Tipo = Literal["ok", "error", "info"]

# Cuántos eventos se guardan y cuántos viajan a la pantalla.
GUARDADOS = 20
VISIBLES = 5


def tipo_de(estado: str, mensaje: str | None) -> Tipo:
    """Éxito, error o paso intermedio, según el estado al que pasó el bot y su mensaje."""
    if mensaje and mensaje.startswith("No se pudo"):
        return "error"
    if estado == "desconectado":
        return "error" if mensaje else "info"
    return "ok" if estado == "conectado" else "info"


class Bitacora:
    def __init__(self) -> None:
        self._eventos: deque[dict] = deque(maxlen=GUARDADOS)
        self._ultima_actividad: datetime | None = None

    def registrar(self, mensaje: str, tipo: Tipo) -> None:
        """Anota un evento. El mismo mensaje seguido no se repite: solo actualiza la hora."""
        ahora = datetime.now()
        self._ultima_actividad = ahora
        if self._eventos and self._eventos[-1]["mensaje"] == mensaje and self._eventos[-1]["tipo"] == tipo:
            self._eventos[-1]["cuando"] = ahora.isoformat(timespec="seconds")
            return
        self._eventos.append({"cuando": ahora.isoformat(timespec="seconds"), "tipo": tipo, "mensaje": mensaje})

    def tocar(self) -> None:
        """El bot comprobó que la sesión sigue viva: cuenta como actividad, sin ensuciar el log."""
        self._ultima_actividad = datetime.now()

    def a_dict(self) -> dict:
        """Para el estado: la última actividad y los eventos más nuevos primero."""
        return {
            "ultima_actividad": self._ultima_actividad.isoformat(timespec="seconds") if self._ultima_actividad else None,
            "eventos": list(reversed(self._eventos))[:VISIBLES],
        }
