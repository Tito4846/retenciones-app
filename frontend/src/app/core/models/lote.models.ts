export type EstadoLote = 'borrador' | 'pendiente' | 'en_revision' | 'rechazado' | 'aprobado' | 'anulado';
/** Empleado prepara los lotes; supervisor además revisa, aprueba o devuelve. */
export type Rol = 'empleado' | 'supervisor';
/** De quién es el error que encontró el supervisor. */
export type OrigenObservacion = 'nuestro' | 'cliente' | 'arca';

export interface Usuario {
  id: string;
  nombre: string;
  roles: Rol[];
  /** Nivel de acceso de ContadorWeb: 200 Administrador General, 100 Administrador, 10 Usuario... */
  nivel?: string;
  nivel_nombre?: string;
  estudio_nombre?: string;
  /** true = la sesión viene de ContadorWeb; false = modo local (usuarios de prueba). */
  conectado?: boolean;
}

export interface Lote {
  id: string;
  empresa: string;
  cuit_empresa: string;
  periodo: string;
  estado: EstadoLote;
  version_vigente: number;
  maker_id: string;
  maker_nombre: string;
  checker_id: string | null;
  checker_nombre: string | null;
  total_libro_erp: string;
  total_fisco: string;
  hash_aprobado: string | null;
  rectifica_a: string | null;
  migrado: number;
  creado_utc: string;
  actualizado_utc: string;
  aprobado_utc: string | null;
  autores: string[];
}

/** Fila del listado: el lote con el resumen de su versión vigente. */
export interface LoteListado extends Lote {
  resumen: ResumenVersion;
  sellada_utc: string | null;
  observaciones_abiertas: number;
  ultimo_rechazo: string | null;
  /** Los impuestos de sus partidas, separados por coma. */
  impuestos: string | null;
  /** Quién armó la versión vigente y cómo. */
  autor_version: string | null;
  origen_version: Version['origen'] | null;
  version_utc: string | null;
  version_correccion: number | null;
  /** Excepciones que siguen sin justificación o sin comentario. */
  sin_completar: number;
  comprobantes: number;
  ultimo_envio: string | null;
}

export interface ResumenVersion {
  partidas?: number;
  por_estado?: Record<string, number>;
  /** Las que no concilian solas y siguen en el lote (versiones viejas no lo traen). */
  excepciones?: number;
  importe_fisco?: string;
  importe_erp?: string;
  /** Con ajustes y sin las filas quitadas. Las versiones viejas no los tienen. */
  importe_sistema?: string;
  importe_declarado?: string;
}

export interface ErrorValidacion {
  etapa?: number;
  codigo: string;
  mensaje: string;
  hoja?: string;
  fila?: number;
  columna?: string;
  clave?: string;
  esperado?: string | null;
  encontrado?: string | null;
}

export interface Validacion {
  valida?: boolean;
  etapa_fallida?: number | null;
  errores?: ErrorValidacion[];
  advertencias?: ErrorValidacion[];
  migrado?: boolean;
  resumen?: { cambiadas?: number };
}

export interface Version {
  id: string;
  numero: number;
  origen: 'generado' | 'subido' | 'edicion' | 'migrado';
  sha256: string;
  nombre_archivo: string;
  autor_id: string;
  autor_nombre: string;
  total_libro_erp: string;
  total_fisco: string;
  resumen: ResumenVersion;
  validacion: Validacion;
  sellada_utc: string | null;
  creado_utc: string;
  /** 1 = la armó el supervisor que revisa, corrigiendo sin devolver el lote. */
  correccion_checker: number;
}

export interface Partida {
  clave: string;
  orden: number;
  estado: string;
  codigo: string;
  flags: string[];
  criterio: string;
  cuit: string;
  razon_social: string;
  impuesto: string;
  certificado: string;
  documento_erp: string;
  fecha_fisco: string | null;
  fecha_erp: string | null;
  importe_fisco: string | null;
  importe_erp: string | null;
  diferencia: string;
  justificacion: string;
  comentario: string;
  adjuntos: number;
  /** Corrección manual del lado del Libro Mayor: importe_fisco e importe_erp no se tocan. */
  importe_ajustado: string | null;
  /** 1 = quitada del cruce: no cuenta en los totales, pero sigue registrada. */
  excluida: number;
  editado_por: string;
  editado_rol: string;
  editado_utc: string | null;
  /** Lo que le falta para poder enviarse (vacío = lista). */
  faltas: string[];
}

/** Lo que se corrige de una partida desde la pantalla. */
export interface CambioPartida {
  justificacion?: string;
  comentario?: string;
  /** null borra el ajuste. */
  importe_ajustado?: string | null;
  excluida?: boolean;
}

export interface Adjunto {
  id: string;
  clave_partida: string | null;
  nombre: string;
  mime: string;
  tamano: number;
  sha256: string;
  autor_nombre: string;
  creado_utc: string;
}

export interface Observacion {
  id: string;
  version: number | null;
  clave_partida: string | null;
  texto: string;
  origen: OrigenObservacion;
  autor_nombre: string;
  creado_utc: string;
  resuelta_utc: string | null;
}

export interface Decision {
  id: string;
  version: number | null;
  tipo: 'envio' | 'rechazo' | 'aprobacion' | 'anulacion';
  motivo: string;
  usuario_nombre: string;
  fecha_utc: string;
}

export interface Exportacion {
  id: string;
  tipo: string;
  nombre: string;
  sha256: string;
  usuario_nombre: string;
  fecha_utc: string;
}

export interface DetalleLote {
  lote: Lote;
  versiones: Version[];
  partidas: Partida[];
  totales: { sistema: string; declarado: string; neta: string };
  adjuntos: Adjunto[];
  controles: Record<string, { ok: boolean; usuario: string; fecha: string }>;
  observaciones: Observacion[];
  decisiones: Decision[];
  exportaciones: Exportacion[];
  /** Lo que impide enviar o aprobar. */
  compuertas: { enviar: ErrorValidacion[]; aprobar: ErrorValidacion[] };
  /** Lo que quedó sin resolver: no frena, lo evalúa quien aprueba. */
  pendientes: ErrorValidacion[];
}

export interface Catalogo {
  estados_partida: string[];
  codigos: Record<string, string>;
  flags: Record<string, string>;
  justificaciones: Record<string, string>;
  justificaciones_con_respaldo: string[];
  codigos_con_respaldo: string[];
  controles: Record<string, string>;
  exportaciones: Record<string, string>;
  adjuntos_permitidos: string[];
  /** Comprobantes que viajan con la confirmación del lote. */
  evidencia_permitida: string[];
  evidencia_max_mb: number;
  motivo_minimo: number;
  /** El servidor deja borrar del todo los lotes cerrados (para probar). */
  eliminar_cerrados: boolean;
}

export interface EventoAuditoria {
  id: number;
  ts_utc: string;
  user_id: string;
  user_nombre: string;
  roles: string;
  ip_address: string;
  action_type: string;
  version_id: string | null;
  detalle: Record<string, unknown>;
  hash: string;
}

export interface Diff {
  desde: number;
  hasta: number;
  agregadas: Partida[];
  quitadas: Partida[];
  cambiadas: { clave: string; razon_social: string; cambios: { campo: string; antes: string | null; despues: string | null }[] }[];
}

export interface Notificacion {
  id: string;
  lote_id: string;
  tipo: string;
  texto: string;
  creado_utc: string;
  leida_utc: string | null;
}

export interface NuevoLote {
  empresa: string;
  cuit_empresa: string;
  periodo: string;
  comentario: string;
}

/** El cuerpo de error de la API de lotes. */
export interface ErrorApi {
  codigo: string;
  mensaje: string;
  errores: ErrorValidacion[];
}
