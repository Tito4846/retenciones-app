import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, map, of, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  Catalogo,
  DetalleLote,
  Diff,
  ErrorApi,
  EventoAuditoria,
  LoteListado,
  Notificacion,
  NuevoLote,
  OrigenObservacion,
} from '../models/lote.models';
import { ConciliacionService } from './conciliacion.service';
import { SesionService } from './sesion.service';

/** Lo que se puede corregir de una partida. `importe_ajustado: null` borra el ajuste. */
export interface CambioPartida {
  justificacion?: string;
  comentario?: string;
  importe_ajustado?: string | null;
  excluida?: boolean;
}

/** Los lotes de conciliación y su circuito de revisión (Maker-Checker). */
@Injectable({ providedIn: 'root' })
export class LotesStore {
  private readonly http = inject(HttpClient);
  private readonly sesion = inject(SesionService);
  private readonly conciliacionService = inject(ConciliacionService);
  private readonly api = `${environment.apiUrl}/lotes`;

  readonly lotes = signal<LoteListado[]>([]);
  readonly catalogo = signal<Catalogo | null>(null);
  readonly detalle = signal<DetalleLote | null>(null);
  readonly notificaciones = signal<Notificacion[]>([]);
  readonly cargando = signal(false);
  readonly ocupado = signal(false);
  /** El último error de una acción, con el detalle por fila si vino. */
  readonly error = signal<ErrorApi | null>(null);

  /** Lo que espera algo de quien está usando el módulo (contador del menú). */
  readonly paraMi = computed(() => {
    const usuario = this.sesion.usuario();
    if (!usuario) return 0;
    return this.lotes().filter((l) => this.esperaA(l, usuario.id)).length;
  });

  constructor() {
    // Al cambiar de usuario, la bandeja se vuelve a pedir con sus permisos.
    effect(() => {
      if (this.sesion.usuario()) {
        this.cargar();
        this.cargarNotificaciones();
      } else {
        this.lotes.set([]);
        this.detalle.set(null);
      }
    });
  }

  esperaA(l: LoteListado, usuarioId: string): boolean {
    if (this.sesion.puedePreparar() && l.maker_id === usuarioId && (l.estado === 'borrador' || l.estado === 'rechazado')) return true;
    if (this.sesion.esSupervisor()) {
      if (l.estado === 'pendiente' && !l.autores.includes(usuarioId)) return true;
      if (l.estado === 'en_revision' && l.checker_id === usuarioId) return true;
    }
    return false;
  }

  cargar(): void {
    if (!this.catalogo()) this.http.get<Catalogo>(`${this.api}/catalogo`).subscribe((c) => this.catalogo.set(c));
    this.cargando.set(true);
    this.http
      .get<{ lotes: LoteListado[] }>(this.api)
      .pipe(finalize(() => this.cargando.set(false)))
      .subscribe({
        next: ({ lotes }) => this.lotes.set(lotes),
        error: (e) => this.error.set(this.leerError(e, 'No se pudo leer la bandeja. Revisá que el backend esté corriendo.')),
      });
  }

  cargarNotificaciones(): void {
    this.http
      .get<{ notificaciones: Notificacion[] }>(`${environment.apiUrl}/notificaciones`)
      .subscribe({ next: ({ notificaciones }) => this.notificaciones.set(notificaciones), error: () => undefined });
  }

  marcarLeida(id: string): void {
    this.http.post(`${environment.apiUrl}/notificaciones/${id}/leida`, {}).subscribe(() => this.cargarNotificaciones());
  }

  abrir(id: string | null): void {
    if (!id) {
      this.detalle.set(null);
      return;
    }
    if (this.detalle()?.lote.id !== id) this.detalle.set(null);
    this.http.get<DetalleLote>(`${this.api}/${id}`).subscribe({
      next: (d) => this.detalle.set(d),
      error: (e) => this.error.set(this.leerError(e, 'No se pudo abrir el lote.')),
    });
  }

  // --- Acciones (devuelven true si salió bien) -------------------------------

  crear(taskId: string, datos: NuevoLote): Observable<DetalleLote | null> {
    this.error.set(null);
    return this.http.post<DetalleLote>(this.api, { task_id: taskId, ...datos }).pipe(
      tap((d) => {
        this.detalle.set(d);
        this.cargar();
      }),
      catchError((e) => {
        this.error.set(this.leerError(e, 'No se pudo crear el lote.'));
        return of(null);
      }),
    );
  }

  regenerar(loteId: string, taskId: string, versionBase: number): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/versiones/regenerar`, { task_id: taskId, version_base: versionBase }));
  }

  subirVersion(loteId: string, archivo: File, versionBase: number): Observable<boolean> {
    const datos = new FormData();
    datos.append('archivo', archivo);
    datos.append('version_base', String(versionBase));
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/versiones`, datos));
  }

  actualizarPartida(loteId: string, clave: string, version: number, cambios: CambioPartida): Observable<boolean> {
    return this.accion(this.http.patch<DetalleLote>(`${this.api}/${loteId}/partidas/${clave}`, { version, ...cambios }));
  }

  subirAdjunto(loteId: string, archivo: File, clave: string | null): Observable<boolean> {
    const datos = new FormData();
    datos.append('archivo', archivo);
    if (clave) datos.append('clave_partida', clave);
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/adjuntos`, datos));
  }

  retirarAdjunto(loteId: string, adjuntoId: string): Observable<boolean> {
    return this.accion(this.http.delete<DetalleLote>(`${this.api}/${loteId}/adjuntos/${adjuntoId}`));
  }

  enviar(loteId: string, version: number, comentario: string): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/enviar`, { version, comentario }));
  }

  tomar(loteId: string): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/tomar`, {}));
  }

  marcarControl(loteId: string, control: string, version: number, ok: boolean): Observable<boolean> {
    return this.accion(this.http.put<DetalleLote>(`${this.api}/${loteId}/controles/${control}`, { version, ok }));
  }

  observar(loteId: string, version: number, texto: string, origen: OrigenObservacion, clave: string | null): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/observaciones`, { version, texto, origen, clave_partida: clave }));
  }

  resolver(loteId: string, observacionId: string, resuelta: boolean): Observable<boolean> {
    return this.accion(this.http.patch<DetalleLote>(`${this.api}/${loteId}/observaciones/${observacionId}`, { resuelta }));
  }

  rechazar(loteId: string, version: number, motivo: string): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/rechazar`, { version, motivo, observaciones: [] }));
  }

  aprobar(loteId: string, version: number): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/aprobar`, { version }));
  }

  anular(loteId: string, motivo: string): Observable<boolean> {
    return this.accion(this.http.post<DetalleLote>(`${this.api}/${loteId}/anular`, { motivo }));
  }

  exportar(loteId: string, tipo: string): void {
    this.error.set(null);
    this.http.post<{ id: string; nombre: string }>(`${this.api}/${loteId}/exportaciones`, { tipo }).subscribe({
      next: (e) => {
        this.bajar(`${this.api}/${loteId}/exportaciones/${e.id}`, e.nombre);
        this.abrir(loteId);
      },
      error: (e) => this.error.set(this.leerError(e, 'No se pudo generar la exportación.')),
    });
  }

  bajarVersion(loteId: string, numero: number, nombre: string): void {
    this.bajar(`${this.api}/${loteId}/versiones/${numero}/excel`, nombre);
  }

  bajarAdjunto(loteId: string, adjuntoId: string, nombre: string): void {
    this.bajar(`${this.api}/${loteId}/adjuntos/${adjuntoId}`, nombre);
  }

  bajarExportacion(loteId: string, exportacionId: string, nombre: string): void {
    this.bajar(`${this.api}/${loteId}/exportaciones/${exportacionId}`, nombre);
  }

  auditoria(loteId: string): Observable<EventoAuditoria[]> {
    return this.http.get<{ eventos: EventoAuditoria[] }>(`${this.api}/${loteId}/auditoria`).pipe(map((r) => r.eventos));
  }

  diff(loteId: string, desde: number, hasta: number): Observable<Diff> {
    return this.http.get<Diff>(`${this.api}/${loteId}/diff`, { params: { desde, hasta } });
  }

  private bajar(url: string, nombre: string): void {
    this.http.get(url, { responseType: 'blob' }).subscribe({
      next: (blob) => this.conciliacionService.descargar({ blob, nombre }),
      error: () => this.error.set({ codigo: 'DESCARGA', mensaje: 'No se pudo descargar el archivo.', errores: [] }),
    });
  }

  private accion(pedido: Observable<DetalleLote>): Observable<boolean> {
    this.ocupado.set(true);
    this.error.set(null);
    return pedido.pipe(
      map((d) => {
        this.detalle.set(d);
        this.cargar();
        this.cargarNotificaciones();
        return true;
      }),
      catchError((e: unknown) => {
        this.error.set(this.leerError(e, 'No se pudo guardar. Revisá que el backend esté corriendo.'));
        return of(false);
      }),
      finalize(() => this.ocupado.set(false)),
    );
  }

  private leerError(e: unknown, porDefecto: string): ErrorApi {
    if (e instanceof HttpErrorResponse) {
      const detalle = e.error?.detail;
      if (detalle && typeof detalle === 'object' && 'mensaje' in detalle) {
        return { codigo: detalle.codigo, mensaje: detalle.mensaje, errores: detalle.errores ?? [] };
      }
      if (typeof detalle === 'string') return { codigo: String(e.status), mensaje: detalle, errores: [] };
    }
    return { codigo: 'RED', mensaje: porDefecto, errores: [] };
  }
}
