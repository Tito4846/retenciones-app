import { Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { LotesStore } from './core/services/lotes.store';
import { SesionService } from './core/services/sesion.service';

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
  private readonly router = inject(Router);

  private readonly url = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      map((e) => (e as NavigationEnd).urlAfterRedirects),
    ),
    { initialValue: '' },
  );
  /** Ingresar y configurar van a pantalla completa, sin el menú. */
  protected readonly sinMenu = computed(() => !this.sesion.usuario() || /^\/(ingresar|configurar)/.test(this.url()));

  protected iniciales(nombre: string): string {
    const partes = nombre.match(/\p{L}+/gu) ?? [];
    return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
  }
}
