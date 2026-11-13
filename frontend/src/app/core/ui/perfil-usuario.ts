import { Component, ElementRef, computed, inject, input, signal } from '@angular/core';
import { Usuario } from '../models/lote.models';
import { SesionService, USUARIOS_DE_PRUEBA } from '../services/sesion.service';

/** «Administrador · Supervisor»: el nivel de ContadorWeb y el rol que le da acá. */
function rolesDe(u: Usuario): string {
  const roles = u.roles.map((r) => (r === 'supervisor' ? 'Supervisor' : 'Empleado')).join(' · ') || 'Sin rol';
  return u.nivel_nombre ? `${u.nivel_nombre} · ${roles}` : roles;
}

function iniciales(nombre: string): string {
  const partes = nombre.match(/\p{L}+/gu) ?? [];
  return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
}

/**
 * Quién usa el módulo, en una línea: avatar, nombre y rol. La identidad la da
 * ContadorWeb, así que no hay «cerrar sesión» ni perfil propio. En modo local
 * (sin ContadorWeb) el botón abre un menú con los usuarios de prueba.
 */
@Component({
  selector: 'app-perfil-usuario',
  host: {
    class: 'relative block',
    '(document:click)': 'fuera($event)',
    '(document:keydown.escape)': 'abierto.set(false)',
  },
  template: `
    @if (sesion.usuario(); as u) {
      <button type="button" (click)="alternar()" [disabled]="!local()"
        [attr.aria-haspopup]="local() ? 'menu' : null" [attr.aria-expanded]="local() ? abierto() : null"
        [title]="plegada() ? u.nombre + ' · ' + roles() : ''"
        class="flex w-full items-center gap-2.5 rounded-lg p-1.5 text-left transition-colors enabled:hover:bg-brand-50 disabled:cursor-default"
        [class.justify-center]="plegada()" [class.bg-brand-50]="abierto()">
        <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-700 text-[11px] font-bold text-white" aria-hidden="true">{{ iniciales() }}</span>
        @if (!plegada()) {
          <span class="min-w-0 flex-1 leading-tight">
            <span class="block truncate text-sm font-semibold">{{ u.nombre }}</span>
            <span class="block truncate text-xs text-muted">{{ roles() }}</span>
          </span>
          @if (local()) {
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4 shrink-0 text-muted" aria-hidden="true"><path d="m7 15 5-5 5 5" /></svg>
          }
        }
      </button>

      @if (abierto()) {
        <div role="menu" aria-label="Usuario de prueba"
          class="absolute z-50 w-64 rounded-xl border border-line bg-white p-1 shadow-lg transition duration-150 ease-out starting:translate-y-1 starting:opacity-0 motion-reduce:transition-none"
          [class]="plegada() ? 'bottom-0 left-full ml-2' : 'bottom-full left-0 mb-2'">
          <p class="px-2.5 pt-1.5 pb-1 text-[10px] font-semibold tracking-wider text-muted uppercase">Desarrollo · usuario de prueba</p>
          @for (p of usuarios; track p.id) {
            <button type="button" role="menuitemradio" [attr.aria-checked]="p.id === sesion.identidadElegida().id" (click)="elegir(p.id)"
              class="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm hover:bg-brand-50">
              <span class="h-1.5 w-1.5 shrink-0 rounded-full" [class]="p.id === sesion.identidadElegida().id ? 'bg-brand-700' : 'bg-transparent'" aria-hidden="true"></span>
              <span class="min-w-0 flex-1 truncate">{{ p.nombre }}</span>
              <span class="shrink-0 text-xs text-muted tabular-nums">{{ p.nivel }}</span>
            </button>
          }
          <p class="mt-1 border-t border-line px-2.5 pt-1.5 pb-1 text-[11px] text-muted">En producción el usuario lo da ContadorWeb.</p>
        </div>
      }
    }
  `,
})
export class PerfilUsuario {
  protected readonly sesion = inject(SesionService);
  private readonly elemento = inject(ElementRef<HTMLElement>);
  protected readonly usuarios = USUARIOS_DE_PRUEBA;

  /** Menú lateral plegado: solo el avatar, y el menú se abre hacia la derecha. */
  readonly plegada = input(false);

  protected readonly abierto = signal(false);
  /** Modo local: sin ContadorWeb se puede cambiar de usuario de prueba. */
  protected readonly local = computed(() => !this.sesion.conectado());
  protected readonly roles = computed(() => {
    const u = this.sesion.usuario();
    return u ? rolesDe(u) : '';
  });
  protected readonly iniciales = computed(() => iniciales(this.sesion.usuario()?.nombre ?? ''));

  protected alternar(): void {
    if (this.local()) this.abierto.update((a) => !a);
  }

  protected elegir(id: string): void {
    this.abierto.set(false);
    if (id !== this.sesion.identidadElegida().id) this.sesion.cambiar(id);
  }

  protected fuera(evento: MouseEvent): void {
    if (this.abierto() && !this.elemento.nativeElement.contains(evento.target as Node)) this.abierto.set(false);
  }
}
