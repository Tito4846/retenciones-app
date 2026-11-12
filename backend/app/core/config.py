from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_BACKEND = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RAIZ_BACKEND / ".env", extra="ignore")

    # Cualquier puerto de localhost ya esta permitido por regex en main.py;
    # aca van los origenes que no son localhost (el front desplegado).
    CORS_ORIGINS: list[str] = []

    # Dias de diferencia aceptados entre la fecha de la retencion en ARCA y la
    # del asiento en Xubio. El asiento suele cargarse con la fecha del cobro,
    # que puede caer unos dias despues de la fecha del certificado.
    TOLERANCIA_DIAS: int = 5

    # Membrete del papel de trabajo.
    EMPRESA: str = ""
    LOGO_EMPRESA: str = str(RAIZ_BACKEND / "app" / "assets" / "logos" / "logo.png")

    # ------------------------------------------------------------------
    # Usuario (ver app/core/usuarios.py)
    # ------------------------------------------------------------------
    # No hay ingreso propio: la identidad la da ContadorWeb con un pase firmado.
    # El MISMO valor en el servicio de ContadorWeb (mínimo 32 caracteres).
    # Vacío = modo local: encabezados X-Usuario-* o el usuario por defecto.
    RETENCIONES_MODULO_SECRETO: str = ""
    # Duración de la sesión que se abre con el pase.
    SESION_HORAS: float = 12
    # Modo local: el usuario cuando el pedido no trae X-Usuario-*.
    USUARIO_POR_DEFECTO_ID: str = "local"
    USUARIO_POR_DEFECTO_NOMBRE: str = "Usuario local"
    # Nivel de ContadorWeb: 200 Administrador General, 100 Administrador, 10 Usuario...
    USUARIO_POR_DEFECTO_NIVEL: str = "100"
    # Módulos del nivel, separados por coma (para niveles que no son de administrador).
    USUARIO_POR_DEFECTO_MODULOS: str = ""
    # Para probar: un supervisor puede borrar del todo los lotes cerrados. Lo aprobado
    # es el respaldo de la DDJJ: esto va en False antes de trabajar con datos reales.
    PERMITIR_ELIMINAR_CERRADOS: bool = True
    # Carpeta con el build del front (dist/<app>/browser). Si existe, el backend
    # sirve la app en el mismo dominio que la API (así se despliega en Render).
    # Vacía o inexistente = solo API (desarrollo con `ng serve`).
    FRONT_DIST: Path | None = None

    # Tamano maximo por archivo subido.
    MAX_MB_ARCHIVO: int = 20

    # Descargas de los bots y perfil de Chrome de Xubio. Fuera de git.
    DIRECTORIO_DATOS: Path = RAIZ_BACKEND / "datos"

    # ------------------------------------------------------------------
    # Chrome de los bots (mismo criterio que Arca-bot)
    # ------------------------------------------------------------------
    # ARCA suele bloquear el modo headless, y el doble factor se completa a
    # mano en la ventana: por defecto se ve.
    HEADLESS: bool = False
    # "chrome" = el Chrome instalado (no hace falta `playwright install`).
    CANAL_CHROME: str | None = "chrome"
    # Minimizar la ventana una vez conectado (sigue trabajando en la barra).
    MINIMIZAR_VENTANA: bool = True

    # ------------------------------------------------------------------
    # ARCA: login y servicio "Mis Retenciones"
    # ------------------------------------------------------------------
    ARCA_URL_LOGIN: str = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
    ARCA_SEL_CUIT_LOGIN: str = "id=F1:username"
    ARCA_SEL_BTN_SIGUIENTE: str = "id=F1:btnSiguiente"
    ARCA_SEL_CLAVE: str = "id=F1:password"
    ARCA_SEL_BTN_INGRESAR: str = "id=F1:btnIngresar"
    ARCA_SEL_BUSCADOR: str = "#buscadorInput"
    # Nombre del servicio tal como aparece en el buscador del portal.
    ARCA_SERVICIO: str = "Mis Retenciones"
    # Mis Retenciones abre la version nueva (mirequa-web); el bot usa la
    # anterior, que es la del formulario con CUIT del retenido, impuesto,
    # tipo y fechas. Se entra tocando "click aqui" del aviso de la nueva y, si
    # no se puede, yendo directo a esta direccion con la sesion abierta.
    ARCA_VERSION_ANTERIOR: bool = True
    ARCA_URL_MISRET: str = "https://seti.afip.gob.ar/misret/consultaMisRetenciones.do?method=getForm"
    # La sesion de ARCA se renueva sola con la CUIT y la clave del ingreso (que
    # quedan solo en memoria mientras la ventana esta abierta): cuando ARCA la
    # vence y, antes, al cumplir estos minutos sin uso. 0 = solo cuando vence.
    # Si el ingreso pidio doble factor no se renueva antes: lo tendria que
    # completar la persona cada vez.
    ARCA_RENOVAR_SESION_MIN: int = 50
    # ARCA cierra el portal a los ~15 minutos sin actividad. Cada estos minutos
    # el bot le pide la pagina del portal con las cookies de la ventana (sin
    # moverla) para que no lo cierre. 0 = no avisar.
    ARCA_MANTENER_PORTAL_MIN: int = 9
    # El portal al que se le avisa. El bot usa el que quedo en la ventana
    # despues del ingreso; este es el respaldo (el visto el 2026-11-11).
    ARCA_URL_PORTAL: str = "https://portalcf.cloud.afip.gob.ar/portal/app/"
    TIMEOUT_LOGIN_MS: int = 30000
    TIMEOUT_NAVEGACION_MS: int = 15000
    # Si el servicio no aparece en este tiempo, la cuenta no esta adherida.
    TIMEOUT_SERVICIO_MS: int = 5000
    # Tiempo extra para completar el doble factor en la ventana (0 = no espera).
    TIMEOUT_2FA_MS: int = 120000
    RETRASO_TIPEO_MS: int = 30

    # ------------------------------------------------------------------
    # Xubio: ingreso por Visma Connect y Libro Mayor por webreport
    # ------------------------------------------------------------------
    XUBIO_URL: str = "https://xubio.com/NXV/vismaConnect/login"
    # Cuenta del estudio (opcional). Con los dos, el bot escribe el ingreso
    # solo; el reCAPTCHA lo resuelve la persona en la ventana.
    XUBIO_USUARIO: str = ""
    XUBIO_CLAVE: str = ""
    # Donde vive el webreport del Libro Mayor. Vacio = el sitio de la ventana
    # del bot una vez que se entro.
    XUBIO_WEB_URL: str = ""
    # Cuenta a pedir en el webreport. El default es todo el arbol: despues se
    # filtran las cuentas de retenciones por nombre.
    XUBIO_CUENTA_REPORT_CODE: str = "ARBOL_ID=1__SEPNO__0"
    # Palabras (separadas por coma) que identifican las cuentas de retenciones
    # sufridas en el plan de cuentas.
    XUBIO_CUENTAS_RETENCIONES: str = "retencion,retenciones,percepcion,percepciones"
    # Direccion de la pantalla de Libro Mayor (la de la barra del navegador).
    # Vacia = el bot la busca en el menu de Xubio.
    XUBIO_URL_LIBRO_MAYOR: str = ""


settings = Settings()
