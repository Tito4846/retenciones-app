import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CuentasService, mensajeDeError } from '../../core/services/cuentas.service';
import { ClaveNueva } from './clave-nueva';

/**
 * Primera vez: la cuenta principal elige su contraseña con el enlace de un solo
 * uso que muestra la consola del backend (/configurar#token). El token va en el
 * fragmento (#): el navegador no lo manda a ningún servidor; acá se lee y se borra
 * de la barra de direcciones.
 */
@Component({
  selector: 'app-configurar',
  imports: [ClaveNueva, RouterLink],
  template: `
    <div class="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div class="w-full max-w-md rounded-2xl border border-line bg-white p-7 shadow-sm">
        @if (hecho()) {
          <h1 class="text-xl font-semibold">Listo</h1>
          <p class="mt-2 text-sm text-muted">
            La cuenta principal ya tiene contraseña. Al ingresar te va a pedir activar el segundo factor con el celular.
          </p>
          <a routerLink="/ingresar" [queryParams]="{ email: cuenta() }"
            class="mt-5 block rounded-lg bg-brand-700 px-4 py-2.5 text-center font-semibold text-white hover:bg-brand-800">Ingresar</a>
        } @else if (!token) {
          <h1 class="text-xl font-semibold">Falta el enlace</h1>
          <p class="mt-2 text-sm text-muted">
            Abrí el enlace completo que aparece en la consola del backend (termina en <code>#</code> y un código).
          </p>
        } @else {
          <h1 class="text-xl font-semibold">Contraseña de la cuenta principal</h1>
          <p class="mt-1 text-sm text-muted">
            {{ cuenta() || 'La cuenta principal' }} da de alta a los demás usuarios y les resetea la contraseña. Elegí una contraseña larga.
          </p>
          @if (error(); as e) {
            <p class="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{{ e }}</p>
          }
          <app-clave-nueva class="mt-5 block" [email]="cuenta()" [enviando]="enviando()" boton="Guardar contraseña" (elegida)="guardar($event)" />
        }
      </div>
    </div>
  `,
})
export class Configurar implements OnInit {
  private readonly cuentas = inject(CuentasService);
  protected token = '';
  protected readonly cuenta = signal('');
  protected readonly error = signal<string | null>(null);
  protected readonly enviando = signal(false);
  protected readonly hecho = signal(false);

  ngOnInit(): void {
    this.token = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (this.token) history.replaceState(null, '', location.pathname);
    this.cuentas.estado().subscribe((e) => {
      this.cuenta.set(e.cuenta_principal);
      if (e.configurada) this.hecho.set(true);
    });
  }

  protected guardar(clave: string): void {
    this.enviando.set(true);
    this.error.set(null);
    this.cuentas.configurar(this.token, clave).subscribe({
      next: () => (this.enviando.set(false), this.hecho.set(true)),
      error: (e) => (this.enviando.set(false), this.error.set(mensajeDeError(e))),
    });
  }
}
