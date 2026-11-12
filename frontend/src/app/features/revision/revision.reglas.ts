import { Adjunto, Catalogo, Partida } from '../../core/models/lote.models';
import { centavos, CONCILIADO } from './revision.util';

/**
 * Reglas del lote sin nada de pantalla: qué color lleva cada partida, qué traba la
 * aprobación y cuánto avanzó. Las usan la tabla, la barra de botones y la bandeja,
 * así las tres dicen siempre lo mismo.
 */

/** Rojo: falta acción o comprobante · Amarillo: diferencia en revisión · Verde: resuelta. */
export type EstadoFila = 'falta_accion' | 'falta_comprobante' | 'en_revision' | 'resuelta' | 'descartada';

/** Faltante de un lado (ARCA o el Mayor): no es una diferencia menor, hay que hacer algo. */
const FALTANTES = new Set(['N01', 'N02']);

export const ESTADO_FILA: Record<EstadoFila, { texto: string; clase: string; punto: string }> = {
  falta_accion: { texto: 'Falta acción', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  falta_comprobante: { texto: 'Falta comprobante', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  en_revision: { texto: 'Diferencia en revisión', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500' },
  resuelta: { texto: 'Conciliado', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  descartada: { texto: 'Descartada', clase: 'bg-zinc-100 text-zinc-600 ring-zinc-200', punto: 'bg-zinc-400' },
};

/** Si la partida necesita un archivo que la respalde (ajustes, errores de carga, diferencias de importe). */
export function pideRespaldo(p: Partida, catalogo: Catalogo | null): boolean {
  if (p.estado === CONCILIADO || p.excluida) return false;
  return !!catalogo && (catalogo.codigos_con_respaldo.includes(p.codigo) || catalogo.justificaciones_con_respaldo.includes(p.justificacion));
}

/**
 * El estado de una fila. `respaldoGeneral`: el lote ya tiene comprobantes sin
 * partida (los de antes de poder soltarlos en cada fila): cuentan para todas.
 */
export function estadoFila(p: Partida, catalogo: Catalogo | null, respaldoGeneral = false): EstadoFila {
  if (p.excluida) return 'descartada';
  if (p.estado === CONCILIADO) return 'resuelta';
  if (p.faltas.length) return FALTANTES.has(p.codigo) ? 'falta_accion' : 'en_revision';
  if (pideRespaldo(p, catalogo) && !p.adjuntos && !respaldoGeneral) return 'falta_comprobante';
  return 'resuelta';
}

/** Lo que todavía no está resuelto: todo lo rojo y lo amarillo. */
export function sinCompletar(estado: EstadoFila): boolean {
  return estado !== 'resuelta' && estado !== 'descartada';
}

export function hayRespaldoGeneral(adjuntos: Adjunto[]): boolean {
  return adjuntos.some((a) => !a.clave_partida);
}

/** Cuántas excepciones del lote siguen sin completar, con la misma regla que pinta las filas. */
export function contarSinCompletar(partidas: Partida[], adjuntos: Adjunto[], catalogo: Catalogo | null): number {
  const general = hayRespaldoGeneral(adjuntos);
  return partidas.filter((p) => sinCompletar(estadoFila(p, catalogo, general))).length;
}

export interface EstadoAprobacion {
  /** Diferencia neta en centavos (ARCA − Mayor con ajustes). */
  neta: number;
  sinCompletar: number;
  /** Quien mira preparó el lote: nunca lo aprueba. */
  autor: boolean;
  /** Lo que traba el servidor (versión inválida, no cuadra). */
  trabas: string[];
}

/** Por qué no se puede aprobar (vacío = se puede). Es lo que dice el tooltip del botón. */
export function motivosSinAprobar(e: EstadoAprobacion, pesos: (v: number) => string): string[] {
  const motivos: string[] = [];
  if (e.autor) motivos.push('No podés aprobar un lote que preparaste vos.');
  if (e.neta !== 0) motivos.push(`La diferencia neta es ${pesos(e.neta / 100)}: tiene que quedar en $ 0,00.`);
  if (e.sinCompletar > 0) motivos.push(`${e.sinCompletar === 1 ? 'Queda 1 excepción' : `Quedan ${e.sinCompletar} excepciones`} sin completar.`);
  return [...motivos, ...e.trabas];
}

/** Porcentaje resuelto de las excepciones, 0 a 100. Sin excepciones = 100. */
export function progreso(totalExcepciones: number, pendientes: number): number {
  if (totalExcepciones <= 0) return 100;
  const hechas = Math.min(totalExcepciones, Math.max(0, totalExcepciones - pendientes));
  return Math.round((hechas / totalExcepciones) * 100);
}

/** «1.234,56», «1234.56» o «$ 1.234» → «1234.56». null si no es un importe. */
export function leerImporte(texto: string): string | null {
  let limpio = texto.replace(/[$\s]/g, '');
  if (!limpio) return null;
  const coma = limpio.lastIndexOf(',');
  const punto = limpio.lastIndexOf('.');
  // El último separador es el decimal; el otro, de miles.
  if (coma > punto) limpio = limpio.replace(/\./g, '').replace(',', '.');
  else if (punto > coma && coma >= 0) limpio = limpio.replace(/,/g, '');
  else if (punto >= 0) {
    // Solo puntos: «1.234» o «1.234.567» son miles (así se escribe en Argentina); «1234.5», decimal.
    const partes = limpio.split('.');
    if (partes.length > 2 || partes[1].length === 3) limpio = partes.join('');
  }
  if (!/^\d+(\.\d{1,2})?$/.test(limpio)) return null;
  return Number(limpio).toFixed(2);
}

/** La diferencia de la fila en centavos, con el ajuste si lo tiene. */
export function diferenciaFila(p: Partida): number {
  return centavos(p.importe_fisco) - centavos(p.importe_ajustado ?? p.importe_erp);
}
