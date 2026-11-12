import { ConexionArca, ConexionXubio } from '../../core/models/conexiones.models';

/**
 * Cómo está cada bot, sin nada de pantalla: la tarjeta solo pinta lo que sale de acá.
 *
 * - conectado: listo para trabajar (verde).
 * - en_curso: entrando, renovando o consultando solo; no hay que hacer nada (azul).
 * - requiere_accion: espera a la persona en la ventana del bot: captcha, doble factor, ingreso a mano (amarillo).
 * - error: se cayó o no pudo entrar (rojo): se ofrece reconectar.
 * - sin_conectar: nunca se conectó o se desconectó a pedido (gris).
 */
export type SaludBot = 'conectado' | 'en_curso' | 'requiere_accion' | 'error' | 'sin_conectar';

export const BADGE: Record<SaludBot, { texto: string; clase: string; punto: string }> = {
  conectado: { texto: 'Conectado', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  en_curso: { texto: 'Conectando…', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600 motion-safe:animate-pulse' },
  requiere_accion: { texto: 'Requiere acción', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500' },
  error: { texto: 'Error de conexión', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  sin_conectar: { texto: 'Sin conectar', clase: 'bg-slate-100 text-slate-600 ring-slate-200', punto: 'bg-slate-400' },
};

/** Los bots piden ayuda siempre igual: «… en la ventana (de Chrome) del bot». */
const PIDE_LA_VENTANA = /en la ventana/i;

export function saludArca(c: ConexionArca | null): SaludBot {
  if (!c) return 'sin_conectar';
  if (c.estado === 'conectado') return 'conectado';
  if (c.estado === 'conectando') return PIDE_LA_VENTANA.test(c.mensaje ?? '') ? 'requiere_accion' : 'en_curso';
  // Desconectado con un mensaje = se cayó o no pudo entrar. Sin mensaje = a pedido.
  return c.mensaje ? 'error' : 'sin_conectar';
}

export function saludXubio(c: ConexionXubio | null): SaludBot {
  if (!c) return 'sin_conectar';
  if (c.estado === 'conectado') return 'conectado';
  if (c.estado === 'esperando_ingreso') return PIDE_LA_VENTANA.test(c.mensaje ?? '') ? 'requiere_accion' : 'en_curso';
  return c.mensaje ? 'error' : 'sin_conectar';
}

/** El texto del badge cuando el estado general no alcanza (ARCA renovando o consultando). */
export function etiquetaArca(c: ConexionArca | null): string | null {
  if (c?.estado !== 'conectado') return null;
  if (c.renovando) return 'Renovando sesión…';
  if (c.consultando) return 'Consultando…';
  return null;
}

const HORA = new Intl.DateTimeFormat('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false });

/** «Hoy 10:45», «Ayer 18:02» o «03/11 09:15». */
export function cuandoLegible(iso: string | null | undefined, ahora = new Date()): string {
  if (!iso) return '';
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return '';
  const dia = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dias = Math.round((dia(ahora) - dia(fecha)) / 86_400_000);
  const hora = HORA.format(fecha);
  if (dias === 0) return `Hoy ${hora}`;
  if (dias === 1) return `Ayer ${hora}`;
  const dd = String(fecha.getDate()).padStart(2, '0');
  const mm = String(fecha.getMonth() + 1).padStart(2, '0');
  return `${dd}/${mm} ${hora}`;
}

/** Solo la hora, para cada línea del mini-log. */
export function hora(iso: string): string {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? '' : HORA.format(fecha);
}
