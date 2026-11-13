import { Component, computed, input } from '@angular/core';
import { pesos } from './revision.util';

/** Los tres números del lote. Recibe centavos. */
@Component({
  selector: 'app-marcador-cruce',
  host: { class: 'grid grid-cols-3 gap-2' },
  template: `
    <div class="flex min-w-0 flex-col justify-center rounded-lg border border-line bg-white px-3 py-1">
      <p class="text-[10px] leading-tight font-semibold tracking-wider text-muted uppercase">Total Sistema</p>
      <p class="truncate text-sm leading-tight font-semibold tabular-nums" [title]="'Libro Mayor con los ajustes: ' + pesos(sistema() / 100)">{{ pesos(sistema() / 100) }}</p>
    </div>
    <div class="flex min-w-0 flex-col justify-center rounded-lg border border-line bg-white px-3 py-1">
      <p class="text-[10px] leading-tight font-semibold tracking-wider text-muted uppercase">Total Declarado</p>
      <p class="truncate text-sm leading-tight font-semibold tabular-nums" [title]="'Informado por ARCA: ' + pesos(declarado() / 100)">{{ pesos(declarado() / 100) }}</p>
    </div>
    <div class="flex min-w-0 flex-col justify-center rounded-lg border px-3 py-1" role="status" aria-live="polite"
      [class]="cuadra() ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-red-300 bg-red-50 text-red-700 '">
      <p class="text-[10px] leading-tight font-bold tracking-wider uppercase">Diferencia Neta {{ cuadra() ? "✓" : "" }}</p>
      <p class="truncate text-base leading-tight font-bold tabular-nums">{{ pesos(neta() / 100) }}</p>
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
