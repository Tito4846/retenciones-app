import { Component, ElementRef, computed, inject, input, model, viewChild } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { EntregaLote } from './entrega-lote';
import { EvidenciaLote } from './evidencia-lote';
import { ExcepcionesLote } from './excepciones-lote';
import { CONCILIADO } from './revision.util';

/** El trabajo del lote: se baja el Excel, se completa afuera, se sube y se adjuntan los comprobantes. */
@Component({
  selector: 'app-gestor-lote',
  imports: [EntregaLote, EvidenciaLote, ExcepcionesLote],
  host: { class: 'block space-y-3' },
  template: `
    @let det = detalle();
    <section class="rounded-xl border border-line bg-white p-3">
      <app-entrega-lote [detalle]="det" [editar]="editar()">
        @if (!editar()) {
          <button type="button" (click)="descargar()" [disabled]="!vigente()"
            [class]="aprobado() ? 'bg-brand-700 text-white hover:bg-brand-800 border-brand-700' : 'bg-white hover:bg-canvas border-line'"
            class="shrink-0 rounded-lg border px-3 py-2 text-sm font-semibold disabled:opacity-50">{{ aprobado() ? 'Descargar Excel aprobado' : 'Descargar Excel' }}</button>
        }
      </app-entrega-lote>
      @if (editar()) {
      <div class="mt-3 grid gap-2 sm:grid-cols-2">
        <button type="button" (click)="descargar()" [disabled]="!vigente()"
          class="flex items-center justify-center gap-2 rounded-lg border border-line bg-white px-4 py-2 font-semibold hover:bg-canvas disabled:opacity-50">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5" aria-hidden="true">
            <path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16" />
          </svg>
          {{ revisar() ? 'Descargar Excel Subido' : 'Descargar Excel de Trabajo' }}
        </button>
          <label class="flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-2 font-semibold text-white hover:bg-brand-800 focus-within:ring-2 focus-within:ring-brand-400"
            [class.opacity-50]="store.ocupado()">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5" aria-hidden="true">
              <path d="M12 16V4m0 0-4 4m4-4 4 4M4 20h16" />
            </svg>
            {{ revisar() ? 'Subir Excel Corregido' : 'Subir Excel Revisado' }}
            <input #selector type="file" accept=".xlsx" class="sr-only" (change)="subir($event)" [disabled]="store.ocupado()" />
          </label>
      </div>
      }
      @if (editar()) {
        <details class="mt-2 text-xs text-muted">
          <summary class="cursor-pointer font-semibold hover:text-brand-700">Cómo completar el Excel</summary>
          <p class="mt-1">
            En la hoja RESUMEN está la vista general. En CONCILIACION, lo que falta va arriba: completá Justificación y Comentario. Para llevar la diferencia a cero usá Importe ajustado, o poné Sí en Excluida contando el motivo.
          </p>
        </details>
      }

      @if (store.errorExcel(); as e) {
        <div role="alert" class="mt-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 transition duration-300 ease-out starting:-translate-y-1 starting:opacity-0 motion-reduce:transition-none">
          <div class="flex items-start gap-2">
            <p class="font-semibold">{{ e.mensaje }}</p>
            <button type="button" (click)="store.errorExcel.set(null)" class="ml-auto text-red-700/70 hover:text-red-900" aria-label="Cerrar">✕</button>
          </div>
          @if (e.errores.length) {
            <ul class="mt-1.5 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">
              @for (x of e.errores; track $index) { <li>{{ x.mensaje }}</li> }
            </ul>
          }
          <div class="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
            @if (editar()) {
              <button type="button" (click)="reintentar()" class="rounded border border-red-300 bg-white px-2 py-1 hover:bg-red-100">Reintentar</button>
            }
            <button type="button" (click)="descargar()" class="px-1 underline hover:text-red-700">Volver a descargar archivo base</button>
          </div>
        </div>
      }
    </section>

    @if (editar()) {
      @if (revisar()) {
        <details class="rounded-xl border border-line bg-white px-3 py-2">
          <summary class="cursor-pointer text-sm font-semibold">Sumar comprobantes</summary>
          <app-evidencia-lote class="mt-2" [revisar]="true" [(archivos)]="evidencia" />
        </details>
      } @else {
        <app-evidencia-lote [revisar]="false" [(archivos)]="evidencia" />
      }
    }

    <!-- Lo principal del lote: va suelto y entero, sin caja ni scroll propio -->
    <section>
      <h3 class="text-base font-semibold" [class]="porResolver() ? 'text-amber-800' : 'text-emerald-700'">
        @if (porResolver(); as n) {
          Conciliaron solas {{ conciliadas() }} de {{ det.partidas.length }} partidas. {{ n === 1 ? 'Queda 1 sin completar' : 'Quedan ' + n + ' sin completar' }}.
        } @else if (excepciones().length) {
          Conciliaron solas {{ conciliadas() }} de {{ det.partidas.length }} partidas. Las otras {{ excepciones().length }} están completas.
        } @else {
          Conciliaron solas todas las partidas.
        }
      </h3>
      @if (excepciones().length) {
        <app-excepciones-lote [partidas]="excepciones()" [editar]="editar()" />
      }
    </section>
  `,
})
export class GestorLote {
  protected readonly store = inject(LotesStore);

  readonly detalle = input.required<DetalleLote>();
  /** Le toca trabajarlo a quien mira: el que prepara, o el supervisor que corrige en revisión. */
  readonly editar = input(false);
  readonly revisar = input(false);
  readonly evidencia = model<File[]>([]);

  private readonly selector = viewChild<ElementRef<HTMLInputElement>>('selector');
  /** Cerrado y aprobado: el Excel de la versión vigente es el definitivo. */
  protected readonly aprobado = computed(() => this.detalle().lote.estado === 'aprobado');
  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly conciliadas = computed(() => this.detalle().partidas.filter((p) => p.estado === CONCILIADO).length);
  /** Todo lo que no concilió solo: el supervisor tiene que poder leer también lo ya justificado. */
  protected readonly excepciones = computed(() => this.detalle().partidas.filter((p) => p.estado !== CONCILIADO));
  protected readonly porResolver = computed(() => this.excepciones().filter((p) => !p.excluida && p.faltas.length).length);

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
