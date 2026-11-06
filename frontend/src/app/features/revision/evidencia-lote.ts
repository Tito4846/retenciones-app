import { Component, computed, inject, input, model, signal } from '@angular/core';
import { LotesStore } from '../../core/services/lotes.store';
import { peso } from './revision.util';

/**
 * Comprobantes que respaldan los cambios manuales. Quedan en el navegador hasta
 * confirmar: viajan junto con «Enviar a Revisión» o «Aprobar y Cerrar Lote», en la
 * misma operación, así un lote nunca queda confirmado a medias.
 */
@Component({
  selector: 'app-evidencia-lote',
  host: { class: 'block' },
  template: `
    <label (dragover)="sobre($event)" (dragleave)="salir($event)" (drop)="soltar($event)"
      class="block cursor-pointer rounded-lg border border-dashed px-4 py-2.5 text-center text-sm transition-colors focus-within:border-brand-400"
      [class]="encima() ? 'border-brand-400 bg-brand-50' : 'border-line bg-white hover:bg-canvas/60'">
      <span class="block font-semibold">Arrastra aquí los PDFs de respaldo (Certificados, Notas de crédito)</span>
      <span class="block text-xs text-muted">Justifican tus ajustes en el Excel · o hacé clic para elegirlos · PDF, JPG o PNG · hasta {{ maxMb() }} MB por archivo</span>
      <input type="file" multiple class="sr-only" [attr.accept]="extensiones().join(',')" (change)="elegir($event)" [disabled]="store.ocupado()"
        aria-label="Elegir comprobantes" />
    </label>

    @if (rechazos().length) {
      <ul class="mt-2 space-y-0.5 text-xs text-red-700" role="alert">
        @for (r of rechazos(); track r) { <li>{{ r }}</li> }
      </ul>
    }

    @if (archivos().length) {
      <ul class="mt-2 flex flex-wrap gap-2">
        @for (a of archivos(); track clave(a)) {
          <li class="flex max-w-full items-center gap-2 rounded-full border border-line bg-white py-1 pr-1.5 pl-3 text-xs">
            <span class="min-w-0 truncate font-semibold" [title]="a.name">{{ a.name }}</span>
            <span class="shrink-0 text-muted tabular-nums">{{ peso(a.size) }}</span>
            <button type="button" (click)="quitar(a)" [disabled]="store.ocupado()" [attr.aria-label]="'Quitar ' + a.name"
              class="shrink-0 rounded-full p-1 text-red-600 hover:bg-red-50 disabled:opacity-50">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5" aria-hidden="true">
                <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
              </svg>
            </button>
          </li>
        }
      </ul>
      <p class="mt-1.5 text-xs text-muted">Todavía no están guardados: se adjuntan al {{ revisar() ? 'aprobar' : 'enviar a revisión' }}.</p>
    }
  `,
})
export class EvidenciaLote {
  protected readonly store = inject(LotesStore);
  protected readonly peso = peso;

  readonly archivos = model<File[]>([]);
  /** Quién confirma: el supervisor aprueba, el que prepara envía. */
  readonly revisar = input(false);

  protected readonly encima = signal(false);
  protected readonly rechazos = signal<string[]>([]);
  protected readonly extensiones = computed(() => this.store.catalogo()?.evidencia_permitida ?? ['.jpeg', '.jpg', '.pdf', '.png']);
  protected readonly maxMb = computed(() => this.store.catalogo()?.evidencia_max_mb ?? 5);

  protected clave(a: File): string {
    return `${a.name}|${a.size}|${a.lastModified}`;
  }

  protected sobre(evento: DragEvent): void {
    evento.preventDefault();
    this.encima.set(true);
  }

  /** Pasar por encima del texto de adentro también dispara dragleave: solo cuenta si sale de la caja. */
  protected salir(evento: DragEvent): void {
    const caja = evento.currentTarget as HTMLElement;
    if (!caja.contains(evento.relatedTarget as Node | null)) this.encima.set(false);
  }

  protected soltar(evento: DragEvent): void {
    evento.preventDefault();
    this.encima.set(false);
    if (!this.store.ocupado()) this.agregar(evento.dataTransfer?.files);
  }

  protected elegir(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    this.agregar(entrada.files);
    entrada.value = '';
  }

  protected quitar(archivo: File): void {
    this.archivos.update((lista) => lista.filter((a) => a !== archivo));
  }

  private agregar(lista: FileList | null | undefined): void {
    const nuevos = [...this.archivos()];
    const rechazos: string[] = [];
    for (const archivo of Array.from(lista ?? [])) {
      const problema = this.problema(archivo);
      if (problema) rechazos.push(`${archivo.name}: ${problema}`);
      else if (!nuevos.some((a) => this.clave(a) === this.clave(archivo))) nuevos.push(archivo);
    }
    this.archivos.set(nuevos);
    this.rechazos.set(rechazos);
  }

  private problema(archivo: File): string | null {
    const extension = archivo.name.slice(archivo.name.lastIndexOf('.')).toLowerCase();
    if (!this.extensiones().includes(extension)) return 'solo se aceptan PDF, JPG o PNG.';
    if (!archivo.size) return 'está vacío.';
    if (archivo.size > this.maxMb() * 1024 * 1024) return `pesa ${peso(archivo.size)} y el límite es ${this.maxMb()} MB.`;
    return null;
  }
}
