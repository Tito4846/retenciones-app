export type CriterioConciliacion = 'certificado' | 'cuit' | 'razon_social' | 'importe_fecha' | 'manual';

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
  total_falta_certificado: number;
  total_no_declarados: number;
  total_manuales?: number;
  total_sugerencias?: number;
  importe_conciliado: number;
  importe_falta_certificado: number;
  importe_no_declarado: number;
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
  falta_certificado: number;
  no_declarado: number;
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

export interface DetalleConciliacion {
  conciliados: Retencion[];
  falta_certificado: Retencion[];
  no_declarados: Retencion[];
}

export type EstadoTarea = 'pendiente' | 'procesando' | 'completado' | 'error';

/** Respuesta final del cruce ARCA vs. Libro Mayor Xubio. */
export interface ConciliacionResultado {
  status: 'completado';
  resumen: ResumenConciliacion;
  detalle: DetalleConciliacion;
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
  | { status: 'error'; mensaje?: string }
  | ConciliacionResultado;

/** Estado unificado que el servicio emite hacia la UI. */
export type ConciliacionEstado =
  | { fase: 'subiendo'; progreso: number }
  | { fase: 'procesando'; taskId: string; paso?: PasoAutomatico | null; detalle?: string | null }
  | { fase: 'completado'; taskId: string; resultado: ConciliacionResultado }
  | { fase: 'error'; mensaje: string };

/** El Excel generado por el backend, listo para descargar. */
export interface ArchivoExportado {
  blob: Blob;
  nombre: string;
}
