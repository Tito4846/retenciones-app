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

const PUNTO: Record<EstadoPaso, string> = {
  hecho: 'bg-emerald-500',
  actual: 'bg-brand-600 ring-4 ring-brand-100',
  pendiente: 'bg-line',
  devuelto: 'bg-red-500 ring-4 ring-red-100',
};

/** El recorrido del lote de un vistazo: quién hizo cada paso y cuándo. */
@Component({
  selector: 'app-seguimiento-lote',
  imports: [DatePipe],
  host: { class: 'block' },
  template: `
    <ol class="grid grid-cols-4 gap-2" aria-label="Recorrido del lote">
      @for (p of pasos(); track p.titulo) {
        <li class="min-w-0" [attr.aria-current]="p.estado === 'actual' ? 'step' : null">
          <div class="flex items-center gap-1.5">
            <span class="h-2 w-2 shrink-0 rounded-full" [class]="punto[p.estado]"></span>
            <p class="truncate text-[11px] font-semibold tracking-wider uppercase" [class]="p.estado === 'pendiente' ? 'text-muted' : p.estado === 'devuelto' ? 'text-red-700' : 'text-ink'">{{ p.titulo }}</p>
          </div>
          <p class="mt-0.5 truncate pl-3.5 text-sm" [class]="p.quien ? 'font-semibold' : 'text-muted'" [title]="p.quien">{{ p.quien || '—' }}</p>
          @if (p.fecha) { <p class="truncate pl-3.5 text-xs text-muted tabular-nums">{{ p.fecha | date: 'dd/MM HH:mm' }}</p> }
        </li>
      }
    </ol>
  `,
})
export class SeguimientoLote {
  protected readonly punto = PUNTO;
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
