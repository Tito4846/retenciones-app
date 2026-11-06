import { DatePipe } from '@angular/common';
import { Component, ElementRef, computed, inject, input, model, viewChild } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { EvidenciaLote } from './evidencia-lote';
import { ORIGEN_VERSION } from './revision.util';

/** El trabajo del lote: se baja el Excel, se completa afuera, se sube y se adjuntan los comprobantes. */
@Component({
  selector: 'app-gestor-lote',
  imports: [DatePipe, EvidenciaLote],
  host: { class: 'block space-y-4' },
  template: `
    @let det = detalle();
    <section class="rounded-xl border border-line bg-white p-4">
      <div class="grid gap-3 sm:grid-cols-2">
        <button type="button" (click)="descargar()" [disabled]="!vigente()"
          class="flex items-center justify-center gap-2 rounded-lg border border-line bg-white px-4 py-3 font-semibold hover:bg-canvas disabled:opacity-50">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5" aria-hidden="true">
            <path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16" />
          </svg>
          Descargar Excel de Trabajo
        </button>
        @if (editar()) {
          <label class="flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-brand-700 px-4 py-3 font-semibold text-white hover:bg-brand-800 focus-within:ring-2 focus-within:ring-brand-400"
            [class.opacity-50]="store.ocupado()">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5" aria-hidden="true">
              <path d="M12 16V4m0 0-4 4m4-4 4 4M4 20h16" />
            </svg>
            Subir Excel Revisado
            <input #selector type="file" accept=".xlsx" class="sr-only" (change)="subir($event)" [disabled]="store.ocupado()" />
          </label>
        }
      </div>
      @if (vigente(); as v) {
        <p class="mt-2 text-xs text-muted">
          Versión vigente: {{ v.numero }} · {{ origen() }} · {{ v.autor_nombre }} · {{ v.creado_utc | date: 'dd/MM HH:mm' }}
        </p>
      }
      @if (editar()) {
        <p class="mt-1 text-xs text-muted">
          En la hoja CONCILIACION completá Justificación y Comentario. Para llevar la diferencia a cero usá Importe ajustado, o poné Sí en Excluida contando el motivo.
        </p>
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
      <app-evidencia-lote [revisar]="revisar()" [(archivos)]="evidencia" />
    }

    @if (det.adjuntos.length) {
      <section>
        <h3 class="text-sm font-semibold">Comprobantes guardados</h3>
        <ul class="mt-2 divide-y divide-line rounded-lg border border-line bg-white text-sm">
          @for (a of det.adjuntos; track a.id) {
            <li class="flex flex-wrap items-center gap-2 px-3 py-2">
              <button type="button" (click)="store.bajarAdjunto(det.lote.id, a.id, a.nombre)" class="min-w-0 truncate font-semibold text-brand-700 hover:underline">{{ a.nombre }}</button>
              <span class="text-xs text-muted">{{ a.autor_nombre }} · {{ a.creado_utc | date: 'dd/MM HH:mm' }}</span>
              @if (editar()) {
                <button type="button" (click)="store.retirarAdjunto(det.lote.id, a.id).subscribe()" [disabled]="store.ocupado()"
                  class="ml-auto text-xs font-semibold text-muted hover:text-red-700 disabled:opacity-50">Quitar</button>
              }
            </li>
          }
        </ul>
      </section>
    }
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
  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly origen = computed(() => {
    const v = this.vigente();
    return v?.correccion_checker ? 'Corrección del supervisor' : (ORIGEN_VERSION[v?.origen ?? ''] ?? v?.origen);
  });

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
