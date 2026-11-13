import { Component, ElementRef, computed, inject, input, viewChild } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { EntregaLote } from './entrega-lote';
import { ExcepcionesLote } from './excepciones-lote';
import { contarSinCompletar } from './revision.reglas';
import { CONCILIADO } from './revision.util';

/** El trabajo del lote: cada excepción se resuelve en su fila. Los botones del Excel están junto a los totales. */
@Component({
  selector: 'app-gestor-lote',
  imports: [EntregaLote, ExcepcionesLote],
  host: { class: 'block space-y-2' },
  template: `
    @let det = detalle();
    <app-entrega-lote [detalle]="det" [editar]="editar()" />

    @if (store.errorExcel(); as e) {
      <div role="alert" class="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 transition duration-300 ease-out starting:-translate-y-1 starting:opacity-0 motion-reduce:transition-none">
        <div class="flex items-start gap-2">
          <p class="font-semibold">{{ e.mensaje }}</p>
          <button type="button" (click)="store.errorExcel.set(null)" class="ml-auto text-red-700/70 hover:text-red-900" aria-label="Cerrar">✕</button>
        </div>
        @if (e.errores.length) {
          <ul class="mt-1 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">
            @for (x of e.errores; track $index) { <li>{{ x.mensaje }}</li> }
          </ul>
        }
        <div class="mt-1.5 flex flex-wrap gap-2 text-xs font-semibold">
          @if (editar()) {
            <button type="button" (click)="reintentar()" class="rounded border border-red-300 bg-white px-2 py-1 hover:bg-red-100">Reintentar</button>
            <input #selector type="file" accept=".xlsx" class="hidden" (change)="subir($event)" />
          }
          <button type="button" (click)="descargar()" class="px-1 underline hover:text-red-700">Volver a descargar archivo base</button>
        </div>
      </div>
    }

    @if (excepciones().length) {
      <app-excepciones-lote [partidas]="excepciones()" [adjuntos]="det.adjuntos" [loteId]="det.lote.id" [version]="det.lote.version_vigente" [editar]="editar()">
        <p class="text-sm font-semibold" [class]="porResolver() ? 'text-amber-800' : 'text-emerald-700'">
          Conciliaron solas {{ conciliadas() }} de {{ det.partidas.length }}.
          @if (porResolver(); as n) { {{ n === 1 ? 'Queda 1 sin completar' : 'Quedan ' + n + ' sin completar' }}. } @else { Las otras {{ excepciones().length }} están completas. }
        </p>
      </app-excepciones-lote>
    } @else {
      <p class="text-sm font-semibold text-emerald-700">Conciliaron solas todas las partidas.</p>
    }
  `,
})
export class GestorLote {
  protected readonly store = inject(LotesStore);

  readonly detalle = input.required<DetalleLote>();
  /** Le toca trabajarlo a quien mira: el que prepara, o el supervisor que corrige en revisión. */
  readonly editar = input(false);
  readonly revisar = input(false);

  private readonly selector = viewChild<ElementRef<HTMLInputElement>>('selector');
  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly conciliadas = computed(() => this.detalle().partidas.filter((p) => p.estado === CONCILIADO).length);
  /** Todo lo que no concilió solo: el supervisor tiene que poder leer también lo ya justificado. */
  protected readonly excepciones = computed(() => this.detalle().partidas.filter((p) => p.estado !== CONCILIADO));
  protected readonly porResolver = computed(() => contarSinCompletar(this.excepciones(), this.detalle().adjuntos, this.store.catalogo()));

  protected descargar(): void {
    const v = this.vigente();
    if (v) this.store.bajarVersion(this.detalle().lote.id, v.numero, v.nombre_archivo);
  }

  protected subir(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    const archivo = entrada.files?.[0];
    entrada.value = '';
    const { id, version_vigente } = this.detalle().lote;
    if (archivo) this.store.subirVersion(id, archivo, version_vigente).subscribe();
  }

  protected reintentar(): void {
    this.store.errorExcel.set(null);
    this.selector()?.nativeElement.click();
  }
}
