import { Component, ElementRef, effect, inject, input, model, output, signal, viewChild } from '@angular/core';

/**
 * Confirmación chiquita pegada al botón que la abrió, para acciones de una fila.
 * Se dibuja `fixed` (no la corta el scroll de la tabla) y se cierra con Esc, al
 * hacer clic afuera o al mover la página.
 *
 *   <app-popconfirm [(abierto)]="x" mensaje="¿Quitar?" (confirmado)="quitar()">
 *     <button>…</button>   ← el disparador; el que lo use decide cuándo abrir
 *   </app-popconfirm>
 */
@Component({
  selector: 'app-popconfirm',
  host: {
    class: 'relative inline-flex',
    '(document:keydown.escape)': 'abierto.set(false)',
    '(document:click)': 'clicAfuera($event)',
    '(window:scroll)': 'abierto.set(false)',
    '(window:resize)': 'abierto.set(false)',
  },
  template: `
    <ng-content />
    @if (abierto()) {
      <div #panel role="alertdialog" aria-modal="false" [attr.aria-label]="mensaje()" (click)="$event.stopPropagation()"
        class="fixed z-50 w-64 rounded-xl border border-line bg-white p-3 text-left text-sm text-ink shadow-xl"
        [style.top.px]="posicion().top" [style.left.px]="posicion().left">
        <p class="font-semibold">{{ mensaje() }}</p>
        @if (detalle()) { <p class="mt-1 text-xs text-muted">{{ detalle() }}</p> }
        <div class="mt-3 flex justify-end gap-2">
          <button #cancelar type="button" (click)="abierto.set(false)" class="rounded-md px-2.5 py-1 text-xs font-semibold text-muted hover:bg-canvas">Cancelar</button>
          <button type="button" (click)="aceptar()" class="rounded-md bg-red-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-red-700">
            {{ textoConfirmar() }}
          </button>
        </div>
      </div>
    }
  `,
})
export class Popconfirm {
  private readonly elemento = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly abierto = model(false);
  readonly mensaje = input.required<string>();
  readonly detalle = input('');
  readonly textoConfirmar = input('Confirmar');
  readonly confirmado = output<void>();

  protected readonly posicion = signal({ top: 0, left: 0 });
  // El foco va a Cancelar: un Enter de más no borra nada.
  private readonly botonCancelar = viewChild<ElementRef<HTMLButtonElement>>('cancelar');

  constructor() {
    effect(() => {
      if (!this.abierto()) return;
      // Debajo del disparador, alineado a su derecha, sin salirse de la ventana.
      const r = this.elemento.nativeElement.getBoundingClientRect();
      const top = r.bottom + 6 + 120 > window.innerHeight ? r.top - 6 - 120 : r.bottom + 6;
      this.posicion.set({ top: Math.max(8, top), left: Math.max(8, Math.min(r.right - 256, window.innerWidth - 264)) });
      queueMicrotask(() => this.botonCancelar()?.nativeElement.focus());
    });
  }

  protected aceptar(): void {
    this.abierto.set(false);
    this.confirmado.emit();
  }

  protected clicAfuera(evento: MouseEvent): void {
    if (this.abierto() && !this.elemento.nativeElement.contains(evento.target as Node)) this.abierto.set(false);
  }
}
