export type EstadoRevision = 'pendiente' | 'en_revision' | 'observado' | 'aprobado';
/** De quién es el error que encontró el revisor. */
export type OrigenObservacion = 'nuestro' | 'cliente' | 'arca';

export interface ObservacionRevision {
  id: string;
  texto: string;
  origen: OrigenObservacion;
  autor: string;
  fecha: string;
  resuelta: boolean;
}

export interface EventoRevision {
  fecha: string;
  accion: string;
  autor: string;
  texto: string;
}

export interface Revision {
  id: string;
  empresa: string;
  periodo: string;
  archivo: string;
  estado: EstadoRevision;
  enviado_por: string;
  revisor: string | null;
  creado: string;
  actualizado: string | null;
  resumen: {
    total_conciliados?: number;
    total_faltan_en_arca?: number;
    total_faltan_en_mayor?: number;
    total_manuales?: number;
    importe_arca?: number;
    importe_mayor?: number;
    diferencia?: number;
    hasta_periodo?: string | null;
  };
  controles: Record<string, boolean>;
  observaciones: ObservacionRevision[];
  historial: EventoRevision[];
}

export interface EnvioRevision {
  empresa: string;
  periodo: string;
  enviado_por: string;
  comentario: string;
}
