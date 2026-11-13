import { DatePipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { hace, iniciales, quienArmo } from './revision.util';

/** Lo que se subió: quién, cuándo, qué cambió, la nota, los comprobantes y lo que no se aplicó. */
@Component({
  selector: 'app-entrega-lote',
  imports: [DatePipe],
  host: { class: 'block' },
  template: `
    @let det = detalle();
    @if (vigente(); as v) {
      <div class="flex min-w-0 items-center gap-2 text-xs" [title]="v.nombre_archivo">
        <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-700 text-[9px] font-bold text-white" aria-hidden="true">{{ iniciales(v.autor_nombre) }}</span>
        <p class="min-w-0 truncate">
          <span class="font-semibold">{{ titulo() }}</span>
          <span class="text-muted"> · v{{ v.numero }} · <span [title]="v.creado_utc | date: 'dd/MM/yyyy HH:mm'">{{ hace(v.creado_utc) }}</span>
            @if (cambiadas(); as n) { · {{ n === 1 ? '1 partida modificada' : n + ' partidas modificadas' }} }</span>
        </p>
      </div>
    }

    @if (nota(); as n) {
      <blockquote class="mt-1.5 rounded-lg bg-brand-50 px-3 py-1.5 text-sm">
        <span class="font-semibold">Nota de {{ n.usuario_nombre }}:</span> {{ n.motivo }}
      </blockquote>
    }

    @if (det.adjuntos.length) {
      <div class="mt-1.5">
        <p class="text-xs font-semibold text-muted">{{ det.adjuntos.length === 1 ? '1 comprobante' : det.adjuntos.length + ' comprobantes' }}</p>
        <ul class="mt-1 flex flex-wrap gap-1.5">
          @for (a of det.adjuntos; track a.id) {
            <li class="flex max-w-full items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1 text-xs" [title]="a.autor_nombre + ' · ' + (a.creado_utc | date: 'dd/MM HH:mm')">
              <button type="button" (click)="store.bajarAdjunto(det.lote.id, a.id, a.nombre)" class="min-w-0 truncate font-semibold text-brand-700 hover:underline">{{ a.nombre }}</button>
              @if (editar()) {
                <button type="button" (click)="store.retirarAdjunto(det.lote.id, a.id).subscribe()" [disabled]="store.ocupado()" [attr.aria-label]="'Quitar ' + a.nombre"
                  class="shrink-0 text-muted hover:text-red-700 disabled:opacity-50">✕</button>
              }
            </li>
          }
        </ul>
      </div>
    }

    @if (avisos().length) {
      <ul class="mt-1.5 max-h-24 list-disc space-y-0.5 overflow-y-auto pl-4 text-xs text-amber-800">
        @for (a of avisos(); track $index) { <li>{{ a.mensaje }}</li> }
      </ul>
    }
  `,
})
export class EntregaLote {
  protected readonly store = inject(LotesStore);
  protected readonly hace = hace;
  protected readonly iniciales = iniciales;

  readonly detalle = input.required<DetalleLote>();
  readonly editar = input(false);

  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly titulo = computed(() => {
    const v = this.vigente();
    return v ? quienArmo(v.autor_nombre, v.origen, v.correccion_checker) : '';
  });
  protected readonly cambiadas = computed(() => this.vigente()?.validacion.resumen?.cambiadas ?? 0);
  protected readonly nota = computed(() => [...this.detalle().decisiones].reverse().find((d) => d.tipo === 'envio' && d.motivo) ?? null);
  /** Lo que el Excel trajo y no se aplicó. Lo que falta completar ya se ve en la tabla. */
  protected readonly avisos = computed(() => (this.vigente()?.validacion.advertencias ?? []).filter((a) => a.etapa !== 4));
}
