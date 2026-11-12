import { HttpClient, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, PLATFORM_ID, computed, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { catchError, firstValueFrom, of } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Rol, Usuario } from '../models/lote.models';

/**
 * Quién usa la app. No hay ingreso propio: la identidad y los roles los va a dar
 * la página principal (el portal desde donde se abre este módulo).
 *
 * Contrato con el backend: cada pedido lleva X-Usuario-Id, X-Usuario-Nombre y
 * X-Usuario-Roles. Mientras la página principal no esté conectada, la identidad
 * sale de un usuario de prueba elegido en el menú (se recuerda en este navegador).
 *
 * Para conectarla: reemplazar `identidadElegida()` por lo que entregue la página
 * principal (por ejemplo, datos que pasa al abrir el módulo); el resto no cambia.
 */

/** Los usuarios para probar los dos roles y el circuito Maker-Checker (dos supervisores). */
export const USUARIOS_DE_PRUEBA: readonly Usuario[] = [
  { id: 'prueba-empleado', nombre: 'Empleado de prueba', roles: ['empleado'] },
  { id: 'prueba-supervisor', nombre: 'Supervisor de prueba', roles: ['supervisor'] },
  { id: 'prueba-supervisor-2', nombre: 'Otro supervisor', roles: ['supervisor'] },
];

const CLAVE = 'retenciones.usuario_prueba';

function leerElegido(): string | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage.getItem(CLAVE) : null;
  } catch {
    return null;
  }
}

@Injectable({ providedIn: 'root' })
export class SesionService {
  private readonly http = inject(HttpClient);
  private readonly navegador = isPlatformBrowser(inject(PLATFORM_ID));

  /** La identidad que se manda en cada pedido. */
  readonly identidadElegida = signal<Usuario>(USUARIOS_DE_PRUEBA.find((u) => u.id === leerElegido()) ?? USUARIOS_DE_PRUEBA[1]);
  /** Lo que confirmó el backend (/api/yo): es lo que usan las pantallas. */
  readonly usuario = signal<Usuario | null>(null);

  /** Arma, justifica y envía lotes: los dos roles. */
  readonly puedePreparar = computed(() => this.tiene('empleado') || this.tiene('supervisor'));
  /** Revisa, aprueba o devuelve, y se pasa lotes de otro supervisor. */
  readonly esSupervisor = computed(() => this.tiene('supervisor'));

  tiene(rol: Rol): boolean {
    return this.usuario()?.roles.includes(rol) ?? false;
  }

  /** Al abrir la app: le pregunta al backend quién es (y con qué roles). */
  iniciar(): Promise<void> {
    if (!this.navegador) return Promise.resolve();
    return firstValueFrom(
      this.http.get<Usuario>(`${environment.apiUrl}/yo`).pipe(
        // Sin backend todavía: se muestra la identidad elegida igual; los pedidos avisan el error.
        catchError(() => of(this.identidadElegida())),
      ),
    ).then((u) => this.usuario.set(u));
  }

  /** Cambia el usuario de prueba: las pantallas se recargan con sus permisos. */
  cambiar(id: string): void {
    const elegido = USUARIOS_DE_PRUEBA.find((u) => u.id === id);
    if (!elegido) return;
    this.identidadElegida.set(elegido);
    try {
      localStorage.setItem(CLAVE, id);
    } catch {
      // Sin almacenamiento (modo privado): queda elegido hasta recargar.
    }
    void this.iniciar();
  }

  /** Los encabezados del contrato con el backend. */
  encabezados(): Record<string, string> {
    const u = this.identidadElegida();
    return { 'X-Usuario-Id': u.id, 'X-Usuario-Nombre': encodeURIComponent(u.nombre), 'X-Usuario-Roles': u.roles.join(',') };
  }
}

/** Le agrega la identidad a cada pedido a la API. */
export const sesionInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(environment.apiUrl)) return next(req);
  return next(req.clone({ setHeaders: inject(SesionService).encabezados() }));
};
