import { Component, input, output, signal } from '@angular/core';

/** Los 10 códigos de recuperación: se ven una sola vez, hay que guardarlos antes de seguir. */
@Component({
  selector: 'app-codigos-recuperacion',
  template: `
    <p class="text-sm text-muted">
      Si perdés el celular, cada uno de estos códigos sirve <strong>una vez</strong> en lugar del código de la app.
      No se vuelven a mostrar: guardalos en un lugar seguro (no en el mismo celular).
    </p>
    <ul class="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-canvas p-3 font-mono text-sm">
      @for (c of codigos(); track c) {
        <li class="rounded bg-white px-2 py-1 text-center select-all">{{ c }}</li>
      }
    </ul>
    <div class="mt-3 flex gap-2">
      <button type="button" (click)="copiar()" class="flex-1 rounded-lg border border-line px-3 py-2 text-sm font-semibold hover:bg-canvas">
        {{ copiado() ? '✓ Copiados' : 'Copiar' }}
      </button>
      <button type="button" (click)="descargar()" class="flex-1 rounded-lg border border-line px-3 py-2 text-sm font-semibold hover:bg-canvas">
        Descargar .txt
      </button>
    </div>
    <label class="mt-4 flex items-center gap-2 text-sm">
      <input type="checkbox" class="h-4 w-4 accent-brand-600" [checked]="guardados()" (change)="guardados.set(!guardados())" />
      Ya los guardé
    </label>
    <button type="button" (click)="listo.emit()" [disabled]="!guardados()"
      class="mt-3 w-full rounded-lg bg-brand-700 px-4 py-2.5 font-semibold text-white hover:bg-brand-800 disabled:opacity-60">
      Continuar
    </button>
  `,
})
export class CodigosRecuperacion {
  readonly codigos = input<string[]>([]);
  readonly email = input('');
  readonly listo = output<void>();

  protected readonly copiado = signal(false);
  protected readonly guardados = signal(false);

  private texto(): string {
    return `Códigos de recuperación de Retenciones ARCA (${this.email()})\nCada uno sirve una sola vez.\n\n${this.codigos().join('\n')}\n`;
  }

  protected copiar(): void {
    navigator.clipboard?.writeText(this.texto()).then(() => this.copiado.set(true));
  }

  protected descargar(): void {
    const url = URL.createObjectURL(new Blob([this.texto()], { type: 'text/plain' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'codigos-recuperacion-retenciones.txt' });
    a.click();
    URL.revokeObjectURL(url);
  }
}
