import { Component, input, model } from '@angular/core';

/**
 * Bloque que arranca cerrado y se abre con un botón sutil. Usa <details> nativo:
 * teclado, lector de pantalla y buscar-en-página andan sin código.
 *
 *   <app-plegable titulo="Cómo funciona la importación">…</app-plegable>
 */
@Component({
  selector: 'app-plegable',
  host: { class: 'block' },
  template: `
    <details [open]="abierto()" (toggle)="abierto.set($any($event.target).open)" class="group">
      <summary
        class="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-sm font-semibold text-brand-700 select-none hover:text-brand-800 focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
          <circle cx="12" cy="12" r="9" /><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01" />
        </svg>
        {{ titulo() }}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5 transition-transform group-open:rotate-180" aria-hidden="true">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </summary>
      <div class="mt-3"><ng-content /></div>
    </details>
  `,
})
export class Plegable {
  readonly titulo = input.required<string>();
  readonly abierto = model(false);
}
