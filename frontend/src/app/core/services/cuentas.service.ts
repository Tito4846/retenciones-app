import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Cuenta, Enrolamiento, PasoIngreso, Rol } from '../models/lote.models';
import { SesionService } from './sesion.service';

const AUTH = `${environment.apiUrl}/auth`;
const USUARIOS = `${environment.apiUrl}/usuarios`;

/** El mensaje que manda la API ({detail: {mensaje}}), o uno genérico. */
export function mensajeDeError(e: unknown): string {
  const error = e as HttpErrorResponse;
  if (error?.status === 0) return 'No hay conexión con el servidor. ¿Está levantado el backend?';
  const detalle = error?.error?.detail;
  if (typeof detalle === 'string') return detalle;
  if (detalle?.mensaje) return detalle.mensaje;
  if (Array.isArray(detalle)) return 'Revisá los datos ingresados.';
  return 'No se pudo completar. Probá de nuevo.';
}

/** Pasos del ingreso, «Mi cuenta» y gestión de usuarios. */
@Injectable({ providedIn: 'root' })
export class CuentasService {
  private readonly http = inject(HttpClient);
  private readonly sesion = inject(SesionService);

  private paso(o: Observable<PasoIngreso>): Observable<PasoIngreso> {
    return o.pipe(tap((r) => this.sesion.aplicar(r)));
  }

  // --- Ingreso
  estado(): Observable<{ configurada: boolean; cuenta_principal: string }> {
    return this.http.get<{ configurada: boolean; cuenta_principal: string }>(`${AUTH}/estado`);
  }

  configurar(token: string, clave: string): Observable<void> {
    return this.http.post<void>(`${AUTH}/configurar`, { token, clave });
  }

  ingresar(email: string, clave: string): Observable<PasoIngreso> {
    return this.paso(this.http.post<PasoIngreso>(`${AUTH}/ingresar`, { email, clave }));
  }

  segundoFactor(desafio: string, codigo: string): Observable<PasoIngreso> {
    return this.paso(this.http.post<PasoIngreso>(`${AUTH}/2fa`, { desafio, codigo }));
  }

  cambiarClaveInicial(desafio: string, nueva: string): Observable<PasoIngreso> {
    return this.paso(this.http.post<PasoIngreso>(`${AUTH}/cambiar-clave-inicial`, { desafio, nueva }));
  }

  enrolar(desafio: string): Observable<Enrolamiento> {
    return this.http.post<Enrolamiento>(`${AUTH}/2fa/enrolar`, { desafio });
  }

  confirmarEnrolamiento(desafio: string, codigo: string): Observable<PasoIngreso> {
    return this.paso(this.http.post<PasoIngreso>(`${AUTH}/2fa/confirmar`, { desafio, codigo }));
  }

  // --- Mi cuenta
  yo(): Observable<Cuenta> {
    return this.http.get<Cuenta>(`${AUTH}/yo`).pipe(tap((c) => this.sesion.actualizarCuenta(c)));
  }

  cambiarClave(actual: string, nueva: string): Observable<PasoIngreso> {
    return this.paso(this.http.post<PasoIngreso>(`${AUTH}/cambiar-clave`, { actual, nueva }));
  }

  iniciarMi2fa(): Observable<Enrolamiento> {
    return this.http.post<Enrolamiento>(`${AUTH}/mi-2fa`, {});
  }

  confirmarMi2fa(desafio: string, codigo: string): Observable<PasoIngreso> {
    return this.http.post<PasoIngreso>(`${AUTH}/mi-2fa/confirmar`, { desafio, codigo });
  }

  desactivarMi2fa(clave: string): Observable<void> {
    return this.http.post<void>(`${AUTH}/mi-2fa/desactivar`, { clave });
  }

  // --- Usuarios
  listar(): Observable<{ usuarios: Cuenta[] }> {
    return this.http.get<{ usuarios: Cuenta[] }>(USUARIOS);
  }

  crear(email: string, nombre: string, rol: Rol): Observable<{ usuario: Cuenta; clave_temporal: string }> {
    return this.http.post<{ usuario: Cuenta; clave_temporal: string }>(USUARIOS, { email, nombre, rol });
  }

  actualizar(id: string, cambios: Partial<Pick<Cuenta, 'nombre' | 'rol' | 'activo' | 'gestiona_usuarios'>>): Observable<Cuenta> {
    return this.http.patch<Cuenta>(`${USUARIOS}/${id}`, cambios);
  }

  resetearClave(id: string): Observable<{ usuario: Cuenta; clave_temporal: string }> {
    return this.http.post<{ usuario: Cuenta; clave_temporal: string }>(`${USUARIOS}/${id}/resetear-clave`, {});
  }

  resetear2fa(id: string): Observable<Cuenta> {
    return this.http.post<Cuenta>(`${USUARIOS}/${id}/resetear-2fa`, {});
  }

  desbloquear(id: string): Observable<Cuenta> {
    return this.http.post<Cuenta>(`${USUARIOS}/${id}/desbloquear`, {});
  }
}
