import { DatePipe } from '@angular/common';
import { Component, DestroyRef, afterNextRender, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ConexionesStore } from '../../core/services/conexiones.store';

@Component({
  selector: 'app-conexiones',
  imports: [DatePipe, RouterLink],
  templateUrl: './conexiones.html',
})
export class Conexiones {
  protected readonly store = inject(ConexionesStore);

  /** Lo que falta completar en el formulario de ARCA (se muestra al enviar). */
  protected readonly faltaArca = signal<string | null>(null);

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => this.store.seguir(destroyRef));
  }

  /**
   * Los datos se leen del formulario al enviarlo y no de signals atados a
   * (input): Chrome autocompleta la CUIT y la clave guardadas sin avisarle a
   * la página hasta que la persona interactúa, y un botón deshabilitado por
   * campos "vacíos" quedaba bloqueado con los campos llenos a la vista.
   */
  protected conectarArca(form: HTMLFormElement): void {
    const datos = new FormData(form);
    const cuit = String(datos.get('cuit') ?? '');
    const clave = String(datos.get('clave') ?? '');

    if (cuit.replace(/\D/g, '').length !== 11) {
      this.faltaArca.set('La CUIT tiene que tener 11 dígitos.');
      return;
    }
    if (!clave) {
      this.faltaArca.set('Falta la clave fiscal.');
      return;
    }
    this.faltaArca.set(null);
    this.store.conectarArca(cuit, clave);
    // La clave no queda en la pantalla una vez enviada.
    (form.elements.namedItem('clave') as HTMLInputElement).value = '';
  }

  protected conectarXubio(form: HTMLFormElement): void {
    const datos = new FormData(form);
    const usuario = String(datos.get('usuario') ?? '').trim();
    const clave = String(datos.get('clave') ?? '');
    // Sin los dos, se abre la ventana y la persona entra a mano (o usa la cuenta del .env).
    this.store.conectarXubio(usuario && clave ? usuario : null, usuario && clave ? clave : null);
    (form.elements.namedItem('clave') as HTMLInputElement).value = '';
  }
}
