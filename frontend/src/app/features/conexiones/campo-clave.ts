import { Component, computed, input, signal } from '@angular/core';

/**
 * Un campo de credenciales con ícono (candado para la clave). El <input> es nativo
 * y con `name`: el formulario lo lee con FormData, igual que antes, así el
 * autocompletado de Chrome sigue funcionando.
 */
@Component({
  selector: 'app-campo-clave',
  host: { class: 'block' },
  template: `
    <label class="block">
      <span class="mb-1 block text-sm font-medium">{{ etiqueta() }}</span>
      <span class="relative block">
        <span class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-400" aria-hidden="true">
          @if (secreto()) {
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4">
              <rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
            </svg>
          } @else {
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4">
              <circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" />
            </svg>
          }
        </span>
        <input [name]="nombre()" [type]="tipo()" [attr.autocomplete]="autocompletar()" [attr.inputmode]="modo()" [attr.placeholder]="ayuda()"
          [attr.spellcheck]="secreto() ? false : null" autocapitalize="off"
          class="w-full rounded-lg border border-line py-2 pl-9 text-sm focus:border-brand-400 focus:ring-2 focus:ring-brand-100 focus:outline-none"
          [class]="secreto() ? 'pr-10' : 'pr-3'" />
        @if (secreto()) {
          <button type="button" (click)="visible.update((v) => !v)" [attr.aria-label]="visible() ? 'Ocultar ' + etiqueta() : 'Mostrar ' + etiqueta()"
            [attr.aria-pressed]="visible()" class="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-ink">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
              @if (visible()) {
                <path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A9.8 9.8 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 0 0 5.4-1.6" />
              } @else {
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />
              }
            </svg>
          </button>
        }
      </span>
    </label>
  `,
})
export class CampoClave {
  readonly nombre = input.required<string>();
  readonly etiqueta = input.required<string>();
  /** true = clave: candado, oculta y con botón para verla. */
  readonly secreto = input(false);
  readonly autocompletar = input<string | null>(null);
  readonly modo = input<string | null>(null);
  readonly ayuda = input<string | null>(null);
  readonly tipoBase = input('text');

  protected readonly visible = signal(false);
  protected readonly tipo = computed(() => (this.secreto() ? (this.visible() ? 'text' : 'password') : this.tipoBase()));
}
