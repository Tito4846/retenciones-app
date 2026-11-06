import { EstadoLote } from '../../core/models/lote.models';

export const ESTADOS: Record<EstadoLote, { titulo: string; clase: string; punto: string }> = {
  borrador: { titulo: 'En preparación', clase: 'bg-slate-100 text-slate-700 ring-slate-200', punto: 'bg-slate-400' },
  pendiente: { titulo: 'Pendiente', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500' },
  en_revision: { titulo: 'En revisión', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600' },
  rechazado: { titulo: 'Devuelto', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  aprobado: { titulo: 'Aprobado', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  anulado: { titulo: 'Anulado', clase: 'bg-zinc-100 text-zinc-500 ring-zinc-200 line-through', punto: 'bg-zinc-400' },
};

export const CONCILIADO = 'Conciliado Automático';

export const ORIGEN_VERSION: Record<string, string> = {
  generado: 'Generada del cruce',
  subido: 'Excel subido',
  edicion: 'Corrección en pantalla',
  migrado: 'Bandeja anterior',
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
  /** Borrar del todo un lote cerrado: lo habilita el servidor, para probar. */
  eliminar: boolean;
}

const PESOS = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', minimumFractionDigits: 2 });

export function pesos(valor: string | number | null | undefined): string {
  return valor === null || valor === undefined || valor === '' ? '—' : PESOS.format(Number(valor));
}

/** Cuánto pasó desde una fecha, para leer de un vistazo: «hace 5 min», «ayer», «hace 3 días». */
export function hace(iso: string | null | undefined, ahora = Date.now()): string {
  if (!iso) return '';
  const minutos = Math.max(0, Math.floor((ahora - new Date(iso).getTime()) / 60_000));
  if (minutos < 1) return 'recién';
  if (minutos < 60) return `hace ${minutos} min`;
  if (minutos < 24 * 60) return `hace ${Math.floor(minutos / 60)} h`;
  const dias = Math.floor(minutos / (24 * 60));
  return dias === 1 ? 'ayer' : `hace ${dias} días`;
}

/** Quién hizo la versión, en una frase: lo primero que mira el supervisor. */
export function quienArmo(autor: string, origen: string, correccion: number | boolean): string {
  if (correccion) return `${autor} corrigió el Excel`;
  if (origen === 'subido') return `${autor} subió el Excel`;
  if (origen === 'edicion') return `${autor} corrigió en pantalla`;
  if (origen === 'migrado') return `${autor} lo trajo de la bandeja anterior`;
  return `${autor} lo generó de la conciliación`;
}

/** «Ana Prueba» → «AP». */
export function iniciales(nombre: string | null | undefined): string {
  return (nombre ?? '').split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
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
