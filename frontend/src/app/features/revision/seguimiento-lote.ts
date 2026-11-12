import { DatePipe } from '@angular/common';
import { Component, computed, input } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';

type EstadoPaso = 'hecho' | 'actual' | 'pendiente' | 'devuelto';

interface Paso {
  titulo: string;
  quien: string;
  fecha: string | null;
  estado: EstadoPaso;
}

const MARCA: Record<EstadoPaso, string> = {
  hecho: 'bg-emerald-600 text-white',
  actual: 'bg-brand-700 text-white ring-4 ring-brand-100',
  pendiente: 'border-2 border-slate-300 bg-white text-slate-500',
  devuelto: 'bg-red-600 text-white ring-4 ring-red-100',
};

const TITULO: Record<EstadoPaso, string> = {
  hecho: 'text-emerald-800',
  actual: 'text-brand-800',
  pendiente: 'text-slate-500',
  devuelto: 'text-red-700',
};

const ESTADO_LEIBLE: Record<EstadoPaso, string> = {
  hecho: 'completado',
  actual: 'en curso',
  pendiente: 'pendiente',
  devuelto: 'devuelto',
};

/** El recorrido del lote de un vistazo: quién hizo cada paso y cuándo terminó (trazabilidad). */
@Component({
  selector: 'app-seguimiento-lote',
  imports: [DatePipe],
  host: { class: 'block' },
  template: `
    <ol class="grid grid-cols-4" aria-label="Recorrido del lote">
      @for (p of pasos(); track p.titulo; let ultimo = $last) {
        <li class="relative min-w-0 pr-2" [attr.aria-current]="p.estado === 'actual' ? 'step' : null">
          <!-- La línea hasta el paso siguiente: verde si este ya se hizo -->
          @if (!ultimo) {
            <span class="absolute top-3 left-7 right-1 h-0.5 rounded-full" [class]="p.estado === 'hecho' ? 'bg-emerald-500' : 'bg-slate-200'" aria-hidden="true"></span>
          }
          <span class="relative flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold" [class]="marca[p.estado]" aria-hidden="true">
            @switch (p.estado) {
              @case ('hecho') {
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="m5 12 5 5L20 7" /></svg>
              }
              @case ('devuelto') { ! }
              @default { {{ $index + 1 }} }
            }
          </span>
          <p class="mt-1.5 truncate text-xs font-bold tracking-wider uppercase" [class]="titulo[p.estado]">
            {{ p.titulo }}<span class="sr-only"> ({{ leible[p.estado] }})</span>
          </p>
          <p class="truncate text-sm" [class]="p.quien ? 'font-semibold text-ink' : 'text-slate-500'" [title]="p.quien">{{ p.quien || '—' }}</p>
          @if (p.fecha) {
            <p class="truncate text-xs font-medium text-slate-600 tabular-nums">
              <time [attr.datetime]="p.fecha" [title]="p.fecha | date: 'dd/MM/yyyy HH:mm:ss'">{{ p.fecha | date: 'dd/MM/yy · HH:mm' }}</time>
            </p>
          }
        </li>
      }
    </ol>
  `,
})
export class SeguimientoLote {
  protected readonly marca = MARCA;
  protected readonly titulo = TITULO;
  protected readonly leible = ESTADO_LEIBLE;
  readonly detalle = input.required<DetalleLote>();

  protected readonly pasos = computed<Paso[]>(() => {
    const { lote, decisiones } = this.detalle();
    const ultima = (tipo: string) => [...decisiones].reverse().find((d) => d.tipo === tipo) ?? null;
    const envio = ultima('envio');
    const rechazo = ultima('rechazo');
    const aprobacion = ultima('aprobacion');
    const { estado } = lote;
    const devuelto = estado === 'rechazado';
    const enviado = ['pendiente', 'en_revision', 'aprobado'].includes(estado);

    return [
      { titulo: 'Preparó', quien: lote.maker_nombre, fecha: lote.creado_utc, estado: estado === 'borrador' ? 'actual' : devuelto ? 'actual' : 'hecho' },
      { titulo: 'Envió', quien: enviado ? (envio?.usuario_nombre ?? '') : '', fecha: enviado ? (envio?.fecha_utc ?? null) : null, estado: enviado ? 'hecho' : 'pendiente' },
      devuelto
        ? { titulo: 'Devolvió', quien: rechazo?.usuario_nombre ?? '', fecha: rechazo?.fecha_utc ?? null, estado: 'devuelto' }
        : {
            titulo: 'Revisa',
            quien: lote.checker_nombre ?? (estado === 'pendiente' ? 'Sin tomar' : ''),
            fecha: null,
            estado: estado === 'aprobado' ? 'hecho' : estado === 'pendiente' || estado === 'en_revision' ? 'actual' : 'pendiente',
          },
      { titulo: 'Aprobó', quien: aprobacion?.usuario_nombre ?? '', fecha: aprobacion?.fecha_utc ?? null, estado: estado === 'aprobado' ? 'hecho' : 'pendiente' },
    ];
  });
}
