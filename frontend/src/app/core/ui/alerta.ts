import { Component, computed, input, output } from '@angular/core';

type Tono = 'info' | 'aviso' | 'error';

const TONOS: Record<Tono, { caja: string; icono: string }> = {
  info: { caja: 'border-brand-100 bg-brand-50 text-brand-800', icono: 'text-brand-600' },
  aviso: { caja: 'border-amber-200 bg-amber-50 text-amber-900', icono: 'text-amber-600' },
  error: { caja: 'border-red-200 bg-red-50 text-red-800', icono: 'text-red-600' },
};

/**
 * Aviso delgado: una línea de título y, si hay más de un mensaje, la lista abajo.
 * Con `descartable`, una ✕ lo cierra (para lo informativo; lo que bloquea, no).
 */
@Component({
  selector: 'app-alerta',
  host: { class: 'block' },
  template: `
    <div class="flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm" [class]="tono().caja" [attr.role]="rol()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mt-0.5 h-4 w-4 shrink-0" [class]="tono().icono" aria-hidden="true">
        <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      </svg>
      <div class="min-w-0 flex-1">
        @if (mensajes().length === 1 && !titulo()) {
          <p>{{ mensajes()[0] }}</p>
        } @else {
          @if (titulo()) { <p class="font-semibold">{{ titulo() }}</p> }
          <ul class="mt-0.5 list-disc space-y-0.5 pl-4">
            @for (m of mensajes(); track m) { <li>{{ m }}</li> }
          </ul>
        }
      </div>
      @if (descartable()) {
        <button type="button" (click)="cerrado.emit()" class="-my-0.5 -mr-1 shrink-0 rounded p-1 opacity-60 hover:bg-white/60 hover:opacity-100" aria-label="Cerrar aviso">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>
      }
    </div>
  `,
})
export class Alerta {
  readonly mensajes = input.required<string[]>();
  readonly titulo = input('');
  readonly nivel = input<Tono>('aviso');
  readonly descartable = input(false);
  readonly cerrado = output<void>();

  protected readonly tono = computed(() => TONOS[this.nivel()]);
  /** alert interrumpe al lector de pantalla: solo para errores. */
  protected readonly rol = computed(() => (this.nivel() === 'error' ? 'alert' : 'status'));
}
