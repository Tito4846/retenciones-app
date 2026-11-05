import { Component, computed, input } from '@angular/core';
import { pesos } from './revision.util';

/** Los tres números del lote. Recibe centavos y se recalcula mientras se edita un importe. */
@Component({
  selector: 'app-marcador-cruce',
  host: { class: 'grid grid-cols-3 gap-2' },
  template: `
    <div class="rounded-xl border border-line bg-white px-3 py-2">
      <p class="text-[11px] font-semibold tracking-wider text-muted uppercase">Total Sistema</p>
      <p class="mt-0.5 truncate font-semibold tabular-nums" [title]="'Libro Mayor con los ajustes: ' + pesos(sistema() / 100)">{{ pesos(sistema() / 100) }}</p>
    </div>
    <div class="rounded-xl border border-line bg-white px-3 py-2">
      <p class="text-[11px] font-semibold tracking-wider text-muted uppercase">Total Declarado</p>
      <p class="mt-0.5 truncate font-semibold tabular-nums" [title]="'Informado por ARCA: ' + pesos(declarado() / 100)">{{ pesos(declarado() / 100) }}</p>
    </div>
    <div class="rounded-xl border px-3 py-2" role="status"
      [class]="cuadra() ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-red-300 bg-red-50 text-red-700 motion-safe:animate-pulse'">
      <p class="text-[11px] font-semibold tracking-wider uppercase">Diferencia Neta</p>
      <p class="mt-0.5 truncate font-semibold tabular-nums">{{ pesos(neta() / 100) }}</p>
    </div>
  `,
})
export class MarcadorCruce {
  readonly sistema = input.required<number>();
  readonly declarado = input.required<number>();
  protected readonly neta = computed(() => this.declarado() - this.sistema());
  protected readonly cuadra = computed(() => this.neta() === 0);
  protected readonly pesos = pesos;
}
