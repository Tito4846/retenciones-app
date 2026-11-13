import { Component, computed, inject, input } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';

/** Bajar y subir el Excel del lote: dos botones chicos al lado de los totales. */
@Component({
  selector: 'app-excel-lote',
  host: { class: 'flex items-stretch gap-1.5' },
  template: `
    <button type="button" (click)="descargar()" [disabled]="!vigente()" [title]="textoDescargar()" [attr.aria-label]="textoDescargar()"
      class="flex items-center gap-1.5 rounded-lg border px-2.5 text-sm font-semibold transition-colors disabled:opacity-50"
      [class]="aprobado() ? 'border-brand-700 bg-brand-700 text-white hover:bg-brand-800' : 'border-line bg-white hover:border-brand-300 hover:text-brand-700'">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
        <path d="M12 3v12m0 0-4-4m4 4 4-4M4 19h16" />
      </svg>
      <span class="hidden sm:inline">Descargar</span>
    </button>
    @if (editar()) {
      <label [title]="textoSubir()"
        class="flex cursor-pointer items-center gap-1.5 rounded-lg bg-brand-700 px-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-800 focus-within:ring-2 focus-within:ring-brand-400"
        [class.opacity-50]="store.ocupado()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
          <path d="M12 16V4m0 0-4 4m4-4 4 4M4 20h16" />
        </svg>
        <span class="hidden sm:inline">Subir</span>
        <input type="file" accept=".xlsx" class="sr-only" [attr.aria-label]="textoSubir()" (change)="subir($event)" [disabled]="store.ocupado()" />
      </label>
    }
  `,
})
export class ExcelLote {
  protected readonly store = inject(LotesStore);

  readonly detalle = input.required<DetalleLote>();
  readonly editar = input(false);
  readonly revisar = input(false);

  protected readonly aprobado = computed(() => this.detalle().lote.estado === 'aprobado');
  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly textoDescargar = computed(() =>
    this.aprobado() ? 'Descargar Excel aprobado' : this.revisar() ? 'Descargar Excel subido' : this.editar() ? 'Descargar Excel de trabajo' : 'Descargar Excel',
  );
  protected readonly textoSubir = computed(() => (this.revisar() ? 'Subir Excel corregido' : 'Subir Excel revisado'));

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
}
