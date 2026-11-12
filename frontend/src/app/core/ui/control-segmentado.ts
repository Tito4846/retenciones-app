import { Component, ElementRef, inject, input, model } from '@angular/core';

export interface OpcionSegmentada<T extends string = string> {
  id: T;
  etiqueta: string;
}

/**
 * Control segmentado: varias opciones excluyentes en una sola pastilla compacta.
 * Se comporta como un grupo de radios (role=radiogroup): Tab entra a la opción
 * elegida y las flechas cambian de opción.
 */
@Component({
  selector: 'app-control-segmentado',
  host: {
    role: 'radiogroup',
    class: 'inline-flex max-w-full flex-wrap gap-0.5 rounded-lg bg-canvas p-0.5 ring-1 ring-line ring-inset',
    '(keydown)': 'teclado($event)',
  },
  template: `
    @for (o of opciones(); track o.id) {
      <button type="button" role="radio" [attr.aria-checked]="valor() === o.id" [tabindex]="valor() === o.id ? 0 : -1" (click)="valor.set(o.id)"
        class="rounded-md px-3 py-1 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:outline-none"
        [class]="valor() === o.id ? 'bg-white text-brand-800 shadow-sm ring-1 ring-brand-200' : 'text-muted hover:text-ink'">
        {{ o.etiqueta }}
      </button>
    }
  `,
})
export class ControlSegmentado<T extends string = string> {
  private readonly elemento = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly opciones = input.required<OpcionSegmentada<T>[]>();
  readonly valor = model.required<T>();

  protected teclado(evento: KeyboardEvent): void {
    const paso = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[evento.key];
    const lista = this.opciones();
    if (!paso || !lista.length) return;
    evento.preventDefault();
    const actual = Math.max(0, lista.findIndex((o) => o.id === this.valor()));
    const siguiente = (actual + paso + lista.length) % lista.length;
    this.valor.set(lista[siguiente].id);
    // El foco sigue a la opción elegida, como en un grupo de radios.
    queueMicrotask(() => this.elemento.nativeElement.querySelectorAll<HTMLButtonElement>('[role=radio]')[siguiente]?.focus());
  }
}
