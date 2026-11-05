import { HttpClient, HttpErrorResponse, HttpInterceptorFn, HttpRequest } from '@angular/common/http';
import { Injectable, PLATFORM_ID, computed, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { Router } from '@angular/router';
import { Observable, catchError, finalize, map, of, shareReplay, switchMap, throwError, timer } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Cuenta, PasoIngreso, Rol, Usuario } from '../models/lote.models';

const API = `${environment.apiUrl}/auth`;
/** Va en /renovar y /salir: un sitio ajeno no puede mandarlo (defensa CSRF junto con SameSite=Strict). */
const ANTI_CSRF = { 'X-Requested-With': 'retenciones' };

/**
 * La sesión de quien usa la app.
 *
 * - El token de acceso (10 min) vive solo en memoria: no va a localStorage ni a
 *   cookies que pueda leer JavaScript. Al recargar la página se pide uno nuevo
 *   con /auth/renovar, que usa la cookie HttpOnly que puso el backend.
 * - Si un pedido vuelve 401, se renueva una vez y se reintenta; si no se puede,
 *   se vuelve a la pantalla de ingreso.
 */
@Injectable({ providedIn: 'root' })
export class SesionService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly navegador = isPlatformBrowser(inject(PLATFORM_ID));

  readonly cuenta = signal<Cuenta | null>(null);
  /** true cuando ya se sabe si hay sesión (después del primer /renovar). */
  readonly lista = signal(false);
  private acceso: string | null = null;
  private accesoVence = 0;
  private renovando: Observable<boolean> | null = null;

  /** Lo que usan las pantallas: id, nombre y roles. */
  readonly usuario = computed<Usuario | null>(() => {
    const c = this.cuenta();
    return c ? { id: c.id, nombre: c.nombre, roles: [c.rol] } : null;
  });
  /** Arma, justifica y envía lotes: los dos roles. */
  readonly puedePreparar = computed(() => this.tiene('empleado') || this.tiene('supervisor'));
  /** Revisa, aprueba o devuelve, y se pasa lotes de otro supervisor. */
  readonly esSupervisor = computed(() => this.tiene('supervisor'));
  /** La cuenta principal y las que ella habilitó. */
  readonly gestionaUsuarios = computed(() => !!this.cuenta()?.principal || !!this.cuenta()?.gestiona_usuarios);

  tiene(rol: Rol): boolean {
    return this.usuario()?.roles.includes(rol) ?? false;
  }

  /** Al abrir la app: si hay una sesión viva (cookie), la retoma. */
  iniciar(): Promise<void> {
    if (!this.navegador) {
      this.lista.set(true);
      return Promise.resolve();
    }
    return new Promise((resolver) => this.renovar().subscribe({ complete: () => (this.lista.set(true), resolver()) }));
  }

  tokenVigente(): string | null {
    return this.acceso && Date.now() < this.accesoVence - 30_000 ? this.acceso : null;
  }

  /** Un solo /renovar a la vez aunque lo pidan varios pedidos juntos. */
  renovar(): Observable<boolean> {
    this.renovando ??= this.http.post<PasoIngreso>(`${API}/renovar`, {}, { withCredentials: true, headers: ANTI_CSRF }).pipe(
      // Otra pestaña renovó en el mismo instante: la cookie nueva ya llegó, se reintenta.
      catchError((e: HttpErrorResponse) =>
        e.status === 409
          ? timer(700).pipe(switchMap(() => this.http.post<PasoIngreso>(`${API}/renovar`, {}, { withCredentials: true, headers: ANTI_CSRF })))
          : throwError(() => e),
      ),
      map((r) => (this.aplicar(r), true)),
      catchError(() => (this.limpiar(), of(false))),
      finalize(() => (this.renovando = null)),
      shareReplay(1),
    );
    return this.renovando;
  }

  /** Respuesta de un paso del ingreso: si dice «listo», queda la sesión abierta. */
  aplicar(r: PasoIngreso): void {
    if (r.paso === 'listo' && r.acceso && r.usuario) {
      this.acceso = r.acceso;
      this.accesoVence = (r.acceso_vence ?? 0) * 1000;
      this.cuenta.set(r.usuario);
    }
  }

  actualizarCuenta(cuenta: Cuenta): void {
    this.cuenta.set(cuenta);
  }

  salir(): void {
    const token = this.acceso;
    this.http
      .post(`${API}/salir`, {}, { withCredentials: true, headers: { ...ANTI_CSRF, ...(token ? { Authorization: `Bearer ${token}` } : {}) } })
      .pipe(catchError(() => of(null)))
      .subscribe(() => {
        this.limpiar();
        this.router.navigateByUrl('/ingresar');
      });
  }

  /** Sin sesión: borra lo que hay en memoria y manda a ingresar (recordando a dónde iba). */
  terminar(volver?: string): void {
    this.limpiar();
    const actual = volver ?? this.router.url;
    if (!actual.startsWith('/ingresar') && !actual.startsWith('/configurar')) {
      this.router.navigate(['/ingresar'], { queryParams: actual && actual !== '/' ? { volver: actual } : {} });
    }
  }

  private limpiar(): void {
    this.acceso = null;
    this.accesoVence = 0;
    this.cuenta.set(null);
  }
}

/** De /auth, los que se usan ya adentro (llevan Bearer). El resto es del ingreso. */
const AUTH_CON_SESION = new Set(['yo', 'cambiar-clave', 'mi-2fa', 'mi-2fa/confirmar', 'mi-2fa/desactivar']);

/** Pedidos del ingreso: no llevan Bearer, sí la cookie (para recibirla o mandarla). */
function esDeIngreso(url: string): boolean {
  if (!url.startsWith(`${API}/`)) return false;
  const ruta = url.slice(API.length + 1).split(/[?#]/)[0];
  return !AUTH_CON_SESION.has(ruta);
}

/** Le agrega el token de acceso a cada pedido a la API; si venció, lo renueva primero. */
export const sesionInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(environment.apiUrl)) return next(req);
  if (esDeIngreso(req.url)) return next(req.clone({ withCredentials: true }));
  const sesion = inject(SesionService);
  const conToken = (r: HttpRequest<unknown>, token: string) =>
    r.clone({ setHeaders: { Authorization: `Bearer ${token}` }, withCredentials: r.url.startsWith(API) });

  const enviar = (token: string | null) => (token ? next(conToken(req, token)) : next(req));
  const primero = sesion.tokenVigente();
  const inicio = primero ? of(primero) : sesion.renovar().pipe(map(() => sesion.tokenVigente()));

  return inicio.pipe(
    switchMap((token) => enviar(token)),
    catchError((error: HttpErrorResponse) => {
      if (error.status !== 401) return throwError(() => error);
      // Puede haber vencido justo en el camino: se renueva una vez y se reintenta.
      return sesion.renovar().pipe(
        switchMap((ok) => {
          const token = sesion.tokenVigente();
          if (!ok || !token) {
            sesion.terminar();
            return throwError(() => error);
          }
          return next(conToken(req, token));
        }),
      );
    }),
  );
};
