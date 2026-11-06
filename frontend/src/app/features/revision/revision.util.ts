import { Adjunto, EstadoLote, Observacion, OrigenObservacion, Partida } from '../../core/models/lote.models';

export const ESTADOS: Record<EstadoLote, { titulo: string; clase: string; punto: string }> = {
  borrador: { titulo: 'En preparación', clase: 'bg-slate-100 text-slate-700 ring-slate-200', punto: 'bg-slate-400' },
  pendiente: { titulo: 'Pendiente', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500' },
  en_revision: { titulo: 'En revisión', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600' },
  rechazado: { titulo: 'Devuelto', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  aprobado: { titulo: 'Aprobado', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  anulado: { titulo: 'Anulado', clase: 'bg-zinc-100 text-zinc-500 ring-zinc-200 line-through', punto: 'bg-zinc-400' },
};

export const CONCILIADO = 'Conciliado Automático';

export const ORIGENES: Record<OrigenObservacion, string> = {
  nuestro: 'Error nuestro',
  cliente: 'Error del cliente',
  arca: 'Dato de ARCA',
};
export const ORDEN_ORIGENES: OrigenObservacion[] = ['nuestro', 'cliente', 'arca'];

/** Etiquetas cortas para los botones de justificación (el texto largo va en el title). */
export const JUSTIFICACION_CORTA: Record<string, string> = {
  aceptada: 'Aceptada',
  temporal: 'Otro período',
  ajuste: 'Ajuste',
  error_carga: 'Error de carga',
};

/** Qué puede hacer quien mira el lote, según su rol y el estado. */
export interface Permisos {
  editar: boolean;
  tomar: boolean;
  reasignar: boolean;
  revisar: boolean;
  /** Supervisor que preparó el lote: lo ve, pero no lo puede aprobar. */
  autor: boolean;
  anular: boolean;
  exportar: boolean;
}

export type AdjuntosPorPartida = Record<string, Adjunto[]>;
export type ObservacionesPorPartida = Record<string, Observacion[]>;

const PESOS = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', minimumFractionDigits: 2 });

export function pesos(valor: string | number | null | undefined): string {
  return valor === null || valor === undefined || valor === '' ? '—' : PESOS.format(Number(valor));
}

/** El peso de un archivo para mostrar: «820 KB», «1,4 MB». */
export function peso(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toLocaleString('es-AR', { maximumFractionDigits: 1 })} MB`;
}

export function periodo(p: string): string {
  const [anio, mes] = p.split('-');
  return mes && anio?.length === 4 ? `${mes}/${anio}` : p || 'Sin período';
}

/** Las cuentas van en centavos enteros: sumando decimales se pierden centavos. */
export function centavos(valor: string | null | undefined): number {
  return Math.round(Number(valor || 0) * 100);
}

/** Lo que cuenta del lado del sistema: el ajuste si lo hay, si no el Libro Mayor. */
export function importeSistema(p: Partida, borrador?: string | null): string | null {
  return (borrador === undefined ? p.importe_ajustado : borrador) ?? p.importe_erp;
}

/** Un importe escrito a mano, en «1.234,56» o «1234.56». undefined = no se entiende; null = vacío. */
export function leerImporte(texto: string): string | null | undefined {
  let t = texto.trim().replace(/^\$\s*/, '');
  if (!t) return null;
  if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
  else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, '');
  return /^\d+(\.\d{1,2})?$/.test(t) ? t : undefined;
}

export function mostrarImporte(valor: string | null): string {
  return valor ? valor.replace('.', ',') : '';
}
