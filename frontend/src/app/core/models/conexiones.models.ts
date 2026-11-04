export type EstadoArca = 'desconectado' | 'conectando' | 'conectado';
export type EstadoXubio = 'desconectado' | 'esperando_ingreso' | 'conectado';

/** El archivo que exportó la persona en la ventana de ARCA del bot. */
export interface DescargaArca {
  nombre: string;
  filas: number;
  /** ISO, hora local del servidor. */
  cuando: string;
}

export interface ImpuestoArca {
  codigo: string;
  descripcion: string;
  /** Tipos que ARCA admite con este impuesto. Vacío = no se pudo saber (todos). */
  tipos?: TipoRetencion[];
  /** "Ganancias", "IVA"... (null si no se reconoce): elige la cuenta de Xubio. */
  impuesto?: string | null;
}

/** Opciones del formulario de Mis Retenciones, leídas por el bot en ARCA. */
export interface FormularioArca {
  cuits: string[];
  impuestos: ImpuestoArca[];
}

export type TipoRetencion = 'Retencion' | 'Percepcion' | 'RetencionPercepcion';

/** Lo que el bot completa en el formulario de ARCA. */
export interface ConsultaArca {
  cuit_retenido: string;
  cuit_agente: string | null;
  /**
   * Códigos de impuesto: el bot repite el formulario con cada uno. Vacío =
   * los detecta solo (por las cuentas del Libro Mayor, o Ganancias e IVA).
   */
  impuestos: string[];
  tipo: TipoRetencion;
  /** YYYY-MM-DD */
  desde: string;
  hasta: string;
}

export interface ConexionArca {
  estado: EstadoArca;
  mensaje: string | null;
  cuit: string | null;
  descarga: DescargaArca | null;
  formulario: FormularioArca | null;
  consultando: boolean;
  /** La sesión venció (o estaba por vencer) y el bot vuelve a entrar solo con los mismos datos. */
  renovando?: boolean;
}

export interface ConexionXubio {
  estado: EstadoXubio;
  mensaje: string | null;
  usuario: string | null;
  /** Hay una cuenta de Xubio en el .env del servidor: no hace falta cargarla. */
  cuenta_env: boolean;
  /** La empresa con la que se entró a Xubio: de ella sale el Libro Mayor. */
  empresa_activa?: string | null;
}

export interface Conexiones {
  arca: ConexionArca;
  xubio: ConexionXubio;
}
