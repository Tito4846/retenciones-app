"""
Comandos de consola para quien administra el servidor.

    uv run python -m app.cli enlace-principal     # vuelve a mostrar el enlace para elegir la contraseña
    uv run python -m app.cli reiniciar-principal  # perdiste la contraseña o el celular de la cuenta principal

`reiniciar-principal` borra la contraseña y el segundo factor de la cuenta
principal, cierra sus sesiones y muestra un enlace nuevo. Queda en la auditoría.
Solo lo puede usar quien tiene acceso a la máquina (y a la base).
"""

import sys

from app.core.config import settings
from app.services import cuentas


def main(argumentos: list[str]) -> int:
    comando = argumentos[0] if argumentos else ""
    if comando == "enlace-principal":
        enlace = cuentas.emitir_enlace_configuracion()
        print(enlace or "La cuenta principal ya tiene contraseña. Si la perdiste: reiniciar-principal")
        return 0
    if comando == "reiniciar-principal":
        respuesta = input(f"Se borra la contraseña y el segundo factor de {settings.CUENTA_PRINCIPAL}. ¿Seguir? (si/no) ")
        if respuesta.strip().lower() not in ("si", "sí", "s"):
            print("Cancelado.")
            return 1
        print("Abrí este enlace para elegir la contraseña nueva (sirve una vez, 24 h):\n")
        print(cuentas.reiniciar_principal())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
