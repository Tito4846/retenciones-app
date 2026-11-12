import { Directive, input } from '@angular/core';

/**
 * Explicación que aparece solo al pasar el mouse: el tooltip nativo del navegador
 * (title), y el mismo texto como descripción para lectores de pantalla.
 *
 * Ponela en un envoltorio y no en el botón si el botón puede estar deshabilitado:
 * un <button disabled> no recibe el hover y el tooltip no aparecería.
 *
 *   <span appAyuda="Baja el Libro Mayor y lo cruza con ARCA"><button>…</button></span>
 */
@Directive({
  selector: '[appAyuda]',
  host: {
    '[attr.title]': 'appAyuda() || null',
    '[attr.aria-description]': 'appAyuda() || null',
  },
})
export class Ayuda {
  readonly appAyuda = input<string | null | undefined>('');
}
