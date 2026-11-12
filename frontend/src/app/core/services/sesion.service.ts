import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, PLATFORM_ID, computed, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Rol, Usuario } from '../models/lote.models';

/**
 * Quién usa la app. No hay ingreso propio: la identidad y el nivel de acceso los
 * da ContadorWeb (la página principal), como con el módulo de Planes de pago.
 *
 * ContadorWeb abre el módulo con `?pase=<pase firmado>`. Al arrancar, el pase se
 * canjea en /api/sesion/entrar por un token de sesión (queda en esta pestaña) que
 * va en `Authorization: Bearer` en cada pedido. El backend traduce el nivel de
 * ContadorWeb a los roles de acá: Administrador General / Administrador ->
 * supervisor; un nivel con «Impuestos/Retenciones» -> empleado.
 *
 * Modo local (backend sin RETENCIONES_MODULO_SECRETO): sin ContadorWeb, la
 * identidad sale de un usuario de prueba elegido en el menú, que se manda en
 * encabezados X-Usuario-Id / -Nombre / -Nivel / -Modulos.
 */

/** Un usuario de prueba con su nivel de ContadorWeb. */
export interface UsuarioDePrueba extends Usuario {
  nivel: string;
  modulos: string[];
}

export const MODULO_RETENCIONES = 'Impuestos/Retenciones';

/** Los niveles de ContadorWeb y el circuito Maker-Checker (dos administradores). */
export const USUARIOS_DE_PRUEBA: readonly UsuarioDePrueba[] = [
  { id: 'prueba-general', nombre: 'Administrador General de prueba', roles: ['supervisor'], nivel: '200', nivel_nombre: 'Administrador General', modulos: ['*'] },
  { id: 'prueba-admin', nombre: 'Administrador de prueba', roles: ['supervisor'], nivel: '100', nivel_nombre: 'Administrador', modulos: ['*'] },
  { id: 'prueba-admin-2', nombre: 'Otro administrador', roles: ['supervisor'], nivel: '100', nivel_nombre: 'Administrador', modulos: ['*'] },
  { id: 'prueba-usuario', nombre: 'Usuario de prueba', roles: ['empleado'], nivel: '10', nivel_nombre: 'Usuario', modulos: [MODULO_RETENCIONES] },
];

const CLAVE = 'retenciones.usuario_prueba';
const CLAVE_TOKEN = 'retenciones.sesion';

function leer(almacen: 'local' | 'session', clave: string): string | null {
  try {
    const a = almacen === 'local' ? globalThis.localStorage : globalThis.sessionStorage;
    return a ? a.getItem(clave) : null;
  } catch {
    return null;
  }
}

function escribir(almacen: 'local' | 'session', clave: string, valor: string | null): void {
  try {
    const a = almacen === 'local' ? globalThis.localStorage : globalThis.sessionStorage;
    if (!a) return;
    if (valor === null) a.removeItem(clave);
    else a.setItem(clave, valor);
  } catch {
    // Sin almacenamiento (modo privado): dura hasta recargar.
  }
}

interface Token {
  token: string;
  expira: number;
}

function tokenGuardado(): Token | null {
  try {
    const t = JSON.parse(leer('session', CLAVE_TOKEN) ?? 'null') as Token | null;
    return t && t.token && t.expira * 1000 > Date.now() ? t : null;
  } catch {
    return null;
  }
}

@Injectable({ providedIn: 'root' })
export class SesionService {
  private readonly http = inject(HttpClient);
  private readonly navegador = isPlatformBrowser(inject(PLATFORM_ID));

  /** Modo local: la identidad que se manda en cada pedido. */
  readonly identidadElegida = signal<UsuarioDePrueba>(USUARIOS_DE_PRUEBA.find((u) => u.id === leer('local', CLAVE)) ?? USUARIOS_DE_PRUEBA[1]);
  /** La sesión abierta con el pase de ContadorWeb. */
  private readonly token = signal<Token | null>(tokenGuardado());
  /** Lo que confirmó el backend (/api/yo): es lo que usan las pantallas. */
  readonly usuario = signal<Usuario | null>(null);
  /** Por qué no hay sesión (pase vencido, sin acceso...): se muestra en lugar de las pantallas. */
  readonly sinSesion = signal<string | null>(null);

  /** true = la sesión viene de ContadorWeb (no se muestran los usuarios de prueba). */
  readonly conectado = computed(() => this.usuario()?.conectado ?? false);
  /** Arma, justifica y envía lotes: los dos roles. */
  readonly puedePreparar = computed(() => this.tiene('empleado') || this.tiene('supervisor'));
  /** Revisa, aprueba o devuelve, y se pasa lotes de otro supervisor. */
  readonly esSupervisor = computed(() => this.tiene('supervisor'));

  tiene(rol: Rol): boolean {
    return this.usuario()?.roles.includes(rol) ?? false;
  }

  /** Al abrir la app: canjea el pase de ContadorWeb (si vino) y pregunta quién es. */
  async iniciar(): Promise<void> {
    if (!this.navegador) return;
    const pase = this.sacarPaseDeLaDireccion();
    if (pase) {
      try {
        const r = await firstValueFrom(this.http.post<Token & { usuario: Usuario }>(`${environment.apiUrl}/sesion/entrar`, { pase }));
        this.guardarToken({ token: r.token, expira: r.expira });
      } catch (e) {
        this.guardarToken(null);
        this.usuario.set(null);
        this.sinSesion.set(mensajeDe(e, 'No se pudo abrir la sesión de ContadorWeb. Volvé a abrir Retenciones desde el menú.'));
        return;
      }
    }
    try {
      this.usuario.set(await firstValueFrom(this.http.get<Usuario>(`${environment.apiUrl}/yo`)));
      this.sinSesion.set(null);
    } catch (e) {
      if (e instanceof HttpErrorResponse && (e.status === 401 || e.status === 403)) {
        if (e.status === 401) this.guardarToken(null);
        this.usuario.set(null);
        this.sinSesion.set(mensajeDe(e, 'Entrá a Retenciones desde ContadorWeb.'));
      } else {
        // Sin backend todavía: se muestra la identidad elegida igual; los pedidos avisan el error.
        this.usuario.set(this.token() ? null : this.identidadElegida());
      }
    }
  }

  /** Modo local: cambia el usuario de prueba y las pantallas se recargan con sus permisos. */
  cambiar(id: string): void {
    const elegido = USUARIOS_DE_PRUEBA.find((u) => u.id === id);
    if (!elegido) return;
    this.identidadElegida.set(elegido);
    escribir('local', CLAVE, id);
    void this.iniciar();
  }

  /** Lo que lleva cada pedido a la API: el token de ContadorWeb o, en modo local, el usuario de prueba. */
  encabezados(): Record<string, string> {
    const t = this.token();
    if (t) return { Authorization: `Bearer ${t.token}` };
    const u = this.identidadElegida();
    return {
      'X-Usuario-Id': u.id,
      'X-Usuario-Nombre': encodeURIComponent(u.nombre),
      'X-Usuario-Nivel': u.nivel,
      'X-Usuario-Modulos': u.modulos.map(encodeURIComponent).join(','),
    };
  }

  private guardarToken(t: Token | null): void {
    this.token.set(t);
    escribir('session', CLAVE_TOKEN, t ? JSON.stringify(t) : null);
  }

  /** El pase va una sola vez: se saca de la barra para que no quede en el historial. */
  private sacarPaseDeLaDireccion(): string | null {
    try {
      const url = new URL(window.location.href);
      const pase = url.searchParams.get('pase');
      if (!pase) return null;
      url.searchParams.delete('pase');
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
      return pase;
    } catch {
      return null;
    }
  }
}

function mensajeDe(e: unknown, porDefecto: string): string {
  const detalle = e instanceof HttpErrorResponse ? e.error?.detail : null;
  return (typeof detalle === 'string' ? detalle : detalle?.mensaje) || porDefecto;
}

/** Le agrega la identidad a cada pedido a la API. */
export const sesionInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(environment.apiUrl) || req.url.endsWith('/sesion/entrar')) return next(req);
  return next(req.clone({ setHeaders: inject(SesionService).encabezados() }));
};
