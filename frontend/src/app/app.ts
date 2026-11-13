import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { LotesStore } from './core/services/lotes.store';
import { SesionService } from './core/services/sesion.service';
import { PerfilUsuario } from './core/ui/perfil-usuario';

const CLAVE_MENU = 'retenciones.menu_plegado';

function leerPlegada(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(CLAVE_MENU) === '1';
  } catch {
    return false;
  }
}

@Component({
  imports: [PerfilUsuario, RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  /** Para el contador de la bandeja de revisión en el menú. */
  protected readonly lotes = inject(LotesStore);
  protected readonly sesion = inject(SesionService);

  /** Menú plegado (solo íconos). Se recuerda en este navegador. */
  protected readonly plegada = signal(leerPlegada());

  protected alternarMenu(): void {
    this.plegada.update((p) => !p);
    try {
      localStorage.setItem(CLAVE_MENU, this.plegada() ? '1' : '0');
    } catch {
      // Sin almacenamiento (modo privado): queda plegada solo hasta recargar.
    }
  }
}
