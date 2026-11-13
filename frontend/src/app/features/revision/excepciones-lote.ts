import { Component, computed, inject, input, signal } from '@angular/core';
import { Adjunto, Partida } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { FilaPartida } from './fila-partida';
import { EstadoFila, estadoFila, hayRespaldoGeneral, sinCompletar } from './revision.reglas';

type Filtro = 'todas' | 'pendientes';

/** Primero lo rojo, después lo amarillo, lo resuelto y al final lo descartado. */
const ORDEN: EstadoFila[] = ['falta_accion', 'falta_comprobante', 'en_revision', 'resuelta', 'descartada'];

/** Las excepciones del lote: se ajustan, descartan y respaldan en la misma fila. */
@Component({
  selector: 'app-excepciones-lote',
  imports: [FilaPartida],
  host: { class: 'block' },
  template: `
    <div class="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
      <ng-content />
    @if (pendientes() && pendientes() < partidas().length) {
      <div class="ml-auto flex gap-1 text-xs font-semibold" role="group" aria-label="Filtrar excepciones">
        <button type="button" (click)="filtro.set('todas')" [attr.aria-pressed]="filtro() === 'todas'" class="rounded-full border px-2.5 py-0.5"
          [class]="filtro() === 'todas' ? 'border-brand-700 bg-brand-700 text-white' : 'border-line bg-white hover:border-brand-300'">Todas ({{ partidas().length }})</button>
        <button type="button" (click)="filtro.set('pendientes')" [attr.aria-pressed]="filtro() === 'pendientes'" class="rounded-full border px-2.5 py-0.5"
          [class]="filtro() === 'pendientes' ? 'border-brand-700 bg-brand-700 text-white' : 'border-line bg-white hover:border-brand-300'">Sin completar ({{ pendientes() }})</button>
      </div>
    }
    </div>
    <table class="w-full table-fixed border-separate border-spacing-0 border-y border-line bg-white text-left text-[13px]" aria-label="Excepciones del lote">
      <colgroup>
        <col />
        <col class="w-18" />
        <col class="w-26" />
        <col class="w-26" />
        <col class="w-26" />
        <col class="w-36" />
        <col class="w-21" />
      </colgroup>
      <thead class="sticky top-13 z-10 text-muted [&_th]:bg-canvas">
        <tr class="[&_th]:px-2 [&_th]:py-1.5 [&_th]:font-semibold [&_th]:whitespace-nowrap">
          <th scope="col">Agente / Proveedor</th>
          <th scope="col">Comprobante</th>
          <th scope="col" class="text-right" title="Importe registrado en el Libro Mayor">Monto Sistema</th>
          <th scope="col" class="text-right">Monto ARCA</th>
          <th scope="col" class="text-right">Diferencia</th>
          <th scope="col">Motivo</th>
          <th scope="col"><span class="sr-only">Acciones</span></th>
        </tr>
      </thead>
      @for (f of filas(); track f.partida.clave) {
        <tbody [appFilaPartida]="f.partida" [estado]="f.estado" [adjuntos]="f.adjuntos" [loteId]="loteId()" [version]="version()" [editar]="editar()"></tbody>
      }
    </table>
  `,
})
export class ExcepcionesLote {
  private readonly store = inject(LotesStore);

  readonly partidas = input.required<Partida[]>();
  readonly adjuntos = input<Adjunto[]>([]);
  readonly loteId = input.required<string>();
  readonly version = input.required<number>();
  readonly editar = input(false);

  protected readonly filtro = signal<Filtro>('todas');

  private readonly conEstado = computed(() => {
    const catalogo = this.store.catalogo();
    const general = hayRespaldoGeneral(this.adjuntos());
    const porPartida = new Map<string, Adjunto[]>();
    for (const a of this.adjuntos()) {
      if (a.clave_partida) porPartida.set(a.clave_partida, [...(porPartida.get(a.clave_partida) ?? []), a]);
    }
    return this.partidas().map((p) => ({ partida: p, estado: estadoFila(p, catalogo, general), adjuntos: porPartida.get(p.clave) ?? [] }));
  });

  protected readonly pendientes = computed(() => this.conEstado().filter((f) => sinCompletar(f.estado)).length);

  protected readonly filas = computed(() => {
    const soloPendientes = this.filtro() === 'pendientes' && this.pendientes() > 0;
    return this.conEstado()
      .filter((f) => !soloPendientes || sinCompletar(f.estado))
      .sort((a, b) => ORDEN.indexOf(a.estado) - ORDEN.indexOf(b.estado) || a.partida.orden - b.partida.orden);
  });
}
