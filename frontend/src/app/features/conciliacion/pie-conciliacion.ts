import { Component, computed, input, output } from '@angular/core';
import { Ayuda } from '../../core/ui/ayuda.directive';

/**
 * Pie fijo de la conciliación (sticky bottom-0): cuántas discrepancias quedan y
 * las dos salidas. Con discrepancias, «Generar Lote» deja de ser el camino feliz:
 * pasa a botón secundario con ⚠ y su tooltip explica por qué. Solo presentación.
 */
@Component({
  selector: 'app-pie-conciliacion',
  imports: [Ayuda],
  host: {
    class:
      'sticky bottom-0 z-30 -mx-10 -mb-10 mt-6 flex flex-wrap items-center gap-3 border-t border-line bg-white/95 px-10 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.05)] backdrop-blur',
  },
  template: `
    <p class="mr-auto flex items-center gap-2 text-sm" role="status">
      @if (discrepancias()) {
        <span class="h-2 w-2 shrink-0 rounded-full bg-amber-500" aria-hidden="true"></span>
        Discrepancias a justificar:
        <strong class="tabular-nums text-amber-800">{{ discrepancias() }} {{ discrepancias() === 1 ? 'partida' : 'partidas' }}</strong>
      } @else {
        <span class="h-2 w-2 shrink-0 rounded-full bg-emerald-500" aria-hidden="true"></span>
        <span class="font-semibold text-emerald-700">Sin discrepancias: todo conciliado</span>
      }
    </p>
    <button type="button" (click)="exportar.emit()" [disabled]="!puedeExportar()"
      class="rounded-lg border border-line bg-white px-4 py-2.5 font-semibold transition-colors hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50">
      {{ exportando() ? 'Generando Excel…' : 'Exportar Excel' }}
    </button>
    <span class="inline-flex" [appAyuda]="ayudaLote()">
      <button type="button" (click)="generarLote.emit()" [disabled]="!puedeExportar() || !puedePreparar()"
        class="inline-flex items-center gap-2 rounded-lg px-4 py-2.5 font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50"
        [class]="discrepancias()
          ? 'border border-amber-400 bg-amber-50 text-amber-900 hover:bg-amber-100'
          : 'bg-brand-700 text-white shadow-sm hover:bg-brand-800'">
        @if (discrepancias()) {
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4 text-amber-600" aria-hidden="true">
            <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          </svg>
        }
        Generar Lote para Revisión
      </button>
    </span>
  `,
})
export class PieConciliacion {
  /** discrepancias_a_justificar: lo que el lote va a llevar como excepciones. */
  readonly discrepancias = input(0);
  readonly puedeExportar = input(false);
  readonly exportando = input(false);
  /** La sesión permite preparar lotes. */
  readonly puedePreparar = input(true);

  readonly exportar = output<void>();
  readonly generarLote = output<void>();

  protected readonly ayudaLote = computed(() => {
    if (!this.puedePreparar()) return 'Tu sesión terminó: volvé a ingresar.';
    const n = this.discrepancias();
    return n
      ? `Quedan ${n} ${n === 1 ? 'discrepancia' : 'discrepancias'}: el lote se genera igual, pero cada una se tiene que justificar en Revisión. Lo ideal es resolverlas antes acá.`
      : 'Todo concilió: el lote va directo a revisión.';
  });
}
