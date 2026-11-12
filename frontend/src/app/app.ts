import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Usuario } from './core/models/lote.models';
import { LotesStore } from './core/services/lotes.store';
import { SesionService, USUARIOS_DE_PRUEBA } from './core/services/sesion.service';

const CLAVE_MENU = 'retenciones.menu_plegado';

function leerPlegada(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(CLAVE_MENU) === '1';
  } catch {
    return false;
  }
}

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  /** Para el contador de la bandeja de revisión en el menú. */
  protected readonly lotes = inject(LotesStore);
  protected readonly sesion = inject(SesionService);
  protected readonly usuariosDePrueba = USUARIOS_DE_PRUEBA;

  protected rolesDe(u: Usuario): string {
    return u.roles.map((r) => (r === 'supervisor' ? 'Supervisor' : 'Empleado')).join(' · ') || 'Sin rol';
  }

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

  protected iniciales(nombre: string): string {
    const partes = nombre.match(/\p{L}+/gu) ?? [];
    return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
  }
}
