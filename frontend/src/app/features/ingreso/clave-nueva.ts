import { Component, computed, input, output, signal } from '@angular/core';

/**
 * Elegir una contraseña nueva (dos veces). La regla de verdad la aplica el
 * backend; acá solo se ayuda a cumplirla antes de mandar: 10 caracteres o más,
 * sin el correo ni el nombre. Sin reglas de mayúsculas o símbolos: una frase
 * larga es más segura y más fácil de recordar.
 */
@Component({
  selector: 'app-clave-nueva',
  template: `
    <form class="space-y-3" (submit)="$event.preventDefault(); enviar()">
      <label class="block text-sm">
        <span class="font-semibold">Contraseña nueva</span>
        <input type="password" name="nueva" autocomplete="new-password" required maxlength="128" [value]="nueva()"
          (input)="nueva.set($any($event.target).value)"
          class="mt-1 w-full rounded-lg border border-line px-3 py-2 outline-none focus:border-brand-400" />
      </label>
      <div class="h-1.5 overflow-hidden rounded-full bg-canvas">
        <div class="h-full rounded-full transition-all" [class]="fuerza().clase" [style.width.%]="fuerza().ancho"></div>
      </div>
      <p class="text-xs" [class]="fuerza().ok ? 'text-emerald-700' : 'text-muted'">{{ fuerza().texto }}</p>
      <label class="block text-sm">
        <span class="font-semibold">Repetila</span>
        <input type="password" name="repetida" autocomplete="new-password" required maxlength="128" [value]="repetida()"
          (input)="repetida.set($any($event.target).value)"
          class="mt-1 w-full rounded-lg border border-line px-3 py-2 outline-none focus:border-brand-400" />
      </label>
      @if (repetida() && repetida() !== nueva()) {
        <p class="text-xs text-red-700">No coinciden.</p>
      }
      <button type="submit" [disabled]="enviando() || !fuerza().ok || nueva() !== repetida()"
        class="w-full rounded-lg bg-brand-700 px-4 py-2.5 font-semibold text-white hover:bg-brand-800 disabled:opacity-60">
        {{ enviando() ? 'Guardando…' : boton() }}
      </button>
    </form>
  `,
})
export class ClaveNueva {
  readonly email = input('');
  readonly enviando = input(false);
  readonly boton = input('Guardar');
  readonly elegida = output<string>();

  protected readonly nueva = signal('');
  protected readonly repetida = signal('');

  protected readonly fuerza = computed(() => {
    const clave = this.nueva();
    const usuario = this.email().split('@')[0].toLowerCase();
    if (!clave) return { ok: false, ancho: 0, clase: 'bg-line', texto: 'Al menos 10 caracteres. Sirve una frase: «el mate de los lunes 2026».' };
    if (clave.length < 10) return { ok: false, ancho: (clave.length / 10) * 50, clase: 'bg-red-500', texto: `Faltan ${10 - clave.length} caracteres.` };
    if (usuario.length >= 4 && clave.toLowerCase().includes(usuario)) {
      return { ok: false, ancho: 40, clase: 'bg-red-500', texto: 'No puede contener tu correo.' };
    }
    if (new Set(clave.toLowerCase()).size < 4) return { ok: false, ancho: 30, clase: 'bg-red-500', texto: 'Es demasiado repetitiva.' };
    const larga = clave.length >= 16;
    return { ok: true, ancho: larga ? 100 : 75, clase: larga ? 'bg-emerald-500' : 'bg-amber-500', texto: larga ? 'Muy buena.' : 'Sirve. Más larga es todavía mejor.' };
  });

  protected enviar(): void {
    if (this.fuerza().ok && this.nueva() === this.repetida()) this.elegida.emit(this.nueva());
  }
}
