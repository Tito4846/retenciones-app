/** La clave con que se cruzó: comprobante (principal), CUIT + importe (secundaria) o las de respaldo. */
export type CriterioConciliacion =
  | 'comprobante'
  | 'cuit_importe'
  | 'razon_social'
  | 'importe_fecha'
  | 'diferencia_menor'
  | 'manual';

/** Un comprobante de retención/percepción tal como lo devuelve el backend. */
export interface Retencion {
  /** "a3" (retención de ARCA) o "m7" (asiento del mayor): con esto se empareja a mano. */
  id?: string;
  cuit: string;
  razon_social: string;
  /** Fecha en formato ISO (YYYY-MM-DD). */
  fecha: string | null;
  importe: number;
  impuesto: string;
  /** Número de certificado de ARCA (conciliados y no declarados). */
  certificado?: string | null;
  regimen?: string | null;
  /** Comprobante del agente (factura, orden de pago) que informa ARCA. */
  comprobante?: string | null;
  /** 'Retencion' o 'Percepcion' (de ARCA o del nombre de la cuenta del mayor). */
  tipo?: string;
  /** Por qué quedó sin par y qué hacer (posterior a ARCA, va en otra cuenta…). */
  nota?: string | null;
  /** Documento, cuenta y descripción del asiento en Xubio (falta certificado). */
  documento?: string | null;
  cuenta?: string | null;
  descripcion?: string | null;
  /** Solo en conciliados: por qué se emparejó y con qué asiento. */
  criterio?: CriterioConciliacion;
  id_mayor?: string;
  fecha_mayor?: string | null;
  diferencia_dias?: number | null;
  importe_mayor?: number;
  diferencia_importe?: number;
  documento_mayor?: string | null;
  cuenta_mayor?: string | null;
}

export interface ResumenConciliacion {
  total_conciliados: number;
  total_faltan_en_arca: number;
  total_faltan_en_mayor: number;
  total_manuales?: number;
  total_sugerencias?: number;
  importe_conciliado: number;
  importe_faltan_en_arca: number;
  importe_faltan_en_mayor: number;
  /** Lo que informa ARCA, lo registrado en el mayor y la diferencia. */
  importe_arca?: number;
  importe_mayor?: number;
  diferencia?: number;
  total_arca: number;
  total_mayor: number;
  tolerancia_dias: number;
  /** Asientos del margen posterior al período sin par (son del mes siguiente). */
  fuera_de_periodo?: number;
  /** Último día del período consultado (flujo automático). */
  hasta_periodo?: string | null;
  /** Filas del Haber (aplicaciones a la DDJJ): no participan del cruce. */
  aplicaciones_mayor?: number;
  /** Por qué la diferencia no da 0; sumados dan `diferencia`. Vacío si da 0. */
  motivos_diferencia?: MotivoDiferencia[];
}

/** Una causa de la diferencia ARCA − Mayor y las filas responsables. */
export interface MotivoDiferencia {
  /** Las dos bandejas de faltantes, o conciliados con importe distinto. */
  motivo: 'faltan_en_mayor' | 'faltan_en_arca' | 'redondeos';
  descripcion: string;
  cantidad: number;
  /** Lo que suma (o resta) a la diferencia. */
  importe: number;
  /** Ids de las filas responsables ("a3" de ARCA, "m7" del mayor). */
  ids: string[];
}

/** ARCA vs. mayor de un impuesto: el control antes de computarlo en la DDJJ. */
export interface ImpuestoConciliado {
  impuesto: string;
  cantidad_arca: number;
  cantidad_mayor: number;
  importe_arca: number;
  importe_mayor: number;
  diferencia: number;
  conciliado: number;
  faltan_en_arca: number;
  faltan_en_mayor: number;
}

/** Un par que el cruce no hizo solo pero que probablemente sea la misma retención. */
export interface Sugerencia {
  id_arca: string;
  id_mayor: string;
  fuerza: 'alta' | 'media';
  motivo: string;
  arca: Retencion;
  mayor: Retencion;
  diferencia_importe: number;
  diferencia_dias: number | null;
}

/** Paso del flujo automático que está corriendo en el backend. */
export type PasoAutomatico = 'xubio' | 'arca' | 'cruce';

/** A, B o C con su cantidad de comprobantes y su monto total. */
export interface CategoriaConciliacion {
  categoria: 'A' | 'B' | 'C';
  clave: keyof DetalleConciliacion;
  nombre: string;
  descripcion: string;
  cantidad: number;
  importe: number;
}

/** A: conciliados; B: faltan en ARCA (están en el Libro Mayor de Xubio); C: faltan en el Libro Mayor de Xubio (están en ARCA). */
export interface DetalleConciliacion {
  conciliados: Retencion[];
  faltan_en_arca: Retencion[];
  faltan_en_mayor: Retencion[];
}

export type EstadoTarea = 'pendiente' | 'procesando' | 'completado' | 'error';

/** Respuesta final del cruce ARCA vs. Libro Mayor Xubio. */
export interface ConciliacionResultado {
  status: 'completado';
  resumen: ResumenConciliacion;
  detalle: DetalleConciliacion;
  categorias?: CategoriaConciliacion[];
  /** Controles de que ARCA y el mayor sean del mismo mes e impuesto. */
  avisos?: string[];
  por_impuesto?: ImpuestoConciliado[];
  sugerencias?: Sugerencia[];
}

/** Respuesta de POST /api/conciliar. */
export interface TareaCreadaResponse {
  task_id: string;
}

/** Respuesta de GET /api/estado/{task_id}. */
export type EstadoTareaResponse =
  | { status: 'pendiente' | 'procesando'; paso?: PasoAutomatico | null; detalle_paso?: string | null }
  | { status: 'error' | 'cancelado'; mensaje?: string }
  | ConciliacionResultado;

/** Estado unificado que el servicio emite hacia la UI. */
export type ConciliacionEstado =
  | { fase: 'subiendo'; progreso: number }
  | { fase: 'procesando'; taskId: string; paso?: PasoAutomatico | null; detalle?: string | null }
  | { fase: 'completado'; taskId: string; resultado: ConciliacionResultado }
  | { fase: 'error'; mensaje: string }
  | { fase: 'cancelado' };

/** El Excel generado por el backend, listo para descargar. */
export interface ArchivoExportado {
  blob: Blob;
  nombre: string;
}
