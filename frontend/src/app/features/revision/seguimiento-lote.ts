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
  hecho: 'bg-emerald-500',
  actual: 'bg-brand-600 ring-2 ring-brand-100',
  pendiente: 'border border-slate-300 bg-white',
  devuelto: 'bg-red-500 ring-2 ring-red-100',
};

const TITULO: Record<EstadoPaso, string> = {
  hecho: 'text-emerald-800',
  actual: 'text-brand-800',
  pendiente: 'text-slate-400',
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
    <ol class="flex min-w-0 items-center gap-1.5 overflow-hidden text-xs whitespace-nowrap" aria-label="Recorrido del lote">
      @for (p of pasos(); track p.titulo; let ultimo = $last) {
        <li class="flex min-w-0 items-center gap-1.5" [attr.aria-current]="p.estado === 'actual' ? 'step' : null"
          [title]="p.fecha ? p.titulo + ' · ' + (p.fecha | date: 'dd/MM/yyyy HH:mm') : p.titulo">
          <span class="h-2 w-2 shrink-0 rounded-full" [class]="marca[p.estado]" aria-hidden="true"></span>
          <span class="font-semibold tracking-wide uppercase" [class]="titulo[p.estado]">{{ p.titulo }}<span class="sr-only"> ({{ leible[p.estado] }})</span></span>
          <span class="min-w-0 truncate" [class]="p.quien ? 'text-ink' : 'text-slate-400'">{{ p.quien || '—' }}</span>
          @if (!ultimo) { <span class="text-slate-300" aria-hidden="true">›</span> }
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
