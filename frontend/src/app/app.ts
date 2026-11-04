import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { RevisionesStore } from './core/services/revisiones.store';

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  /** Para el contador de la bandeja de revisión en el menú. */
  protected readonly revisiones = inject(RevisionesStore);

  constructor() {
    this.revisiones.cargar();
  }
}
