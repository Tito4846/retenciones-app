import { Component, ElementRef, effect, input, model, output, viewChild } from '@angular/core';

/**
 * Confirmación antes de una acción que no se deshace sola. Usa <dialog> nativo:
 * atrapa el foco, cierra con Esc y deja el resto de la página inerte.
 *
 *   <app-confirm-modal [(abierto)]="confirmar" titulo="…" (confirmado)="hacer()">Detalle</app-confirm-modal>
 */
@Component({
  selector: 'app-confirm-modal',
  template: `
    <dialog #dialogo (close)="abierto.set(false)" (click)="alClicFuera($event)" [attr.aria-labelledby]="id + '-titulo'"
      class="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-2xl border border-line bg-white p-0 text-ink shadow-xl backdrop:bg-slate-900/40 backdrop:backdrop-blur-sm">
      <div class="p-5">
        <div class="flex items-start gap-3">
          <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full" [class]="peligro() ? 'bg-red-100 text-red-600' : 'bg-brand-50 text-brand-700'" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5">
              <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
            </svg>
          </span>
          <div class="min-w-0">
            <h2 [id]="id + '-titulo'" class="text-base font-semibold">{{ titulo() }}</h2>
            <div class="mt-1 text-sm text-muted"><ng-content /></div>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <!-- El foco arranca en Cancelar: Enter por error no ejecuta la acción -->
          <button type="button" autofocus (click)="abierto.set(false)" class="rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-canvas">
            {{ textoCancelar() }}
          </button>
          <button type="button" (click)="confirmar()" class="rounded-lg px-4 py-2 text-sm font-semibold text-white"
            [class]="peligro() ? 'bg-red-600 hover:bg-red-700' : 'bg-brand-700 hover:bg-brand-800'">
            {{ textoConfirmar() }}
          </button>
        </div>
      </div>
    </dialog>
  `,
})
export class ConfirmModal {
  private static siguiente = 0;
  protected readonly id = `confirmar-${ConfirmModal.siguiente++}`;

  readonly abierto = model(false);
  readonly titulo = input.required<string>();
  readonly textoConfirmar = input('Confirmar');
  readonly textoCancelar = input('Cancelar');
  /** Rojo: la acción corta algo que está andando. */
  readonly peligro = input(false);
  readonly confirmado = output<void>();

  private readonly dialogo = viewChild.required<ElementRef<HTMLDialogElement>>('dialogo');

  constructor() {
    effect(() => {
      const d = this.dialogo().nativeElement;
      if (this.abierto() && !d.open) d.showModal();
      if (!this.abierto() && d.open) d.close();
    });
  }

  protected confirmar(): void {
    this.abierto.set(false);
    this.confirmado.emit();
  }

  /** Un clic en el fondo oscuro (fuera de la caja) cancela. */
  protected alClicFuera(evento: MouseEvent): void {
    if (evento.target === this.dialogo().nativeElement) this.abierto.set(false);
  }
}
