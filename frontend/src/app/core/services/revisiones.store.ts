import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, map, of, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { EnvioRevision, EstadoRevision, OrigenObservacion, Revision } from '../models/revision.models';
import { ConciliacionService } from './conciliacion.service';

const CLAVE_AUTOR = 'revision.autor';

/** La bandeja de revisión: los papeles de trabajo enviados a verificar y sus estados. */
@Injectable({ providedIn: 'root' })
export class RevisionesStore {
  private readonly http = inject(HttpClient);
  private readonly conciliacionService = inject(ConciliacionService);
  private readonly apiUrl = `${environment.apiUrl}/revisiones`;

  readonly revisiones = signal<Revision[]>([]);
  /** Los controles que se tildan antes de aprobar: {clave: texto}. */
  readonly controles = signal<Record<string, string>>({});
  readonly cargando = signal(false);
  readonly error = signal<string | null>(null);
  readonly ocupada = signal<string | null>(null);

  /** Quien firma lo que hace en la bandeja (queda en el historial). */
  readonly autor = signal(this.leerAutor());

  readonly pendientes = computed(() => this.revisiones().filter((r) => r.estado !== 'aprobado').length);

  cargar(): void {
    this.cargando.set(true);
    this.http
      .get<{ revisiones: Revision[]; controles: Record<string, string> }>(this.apiUrl)
      .pipe(finalize(() => this.cargando.set(false)))
      .subscribe({
        next: ({ revisiones, controles }) => {
          this.revisiones.set(revisiones);
          this.controles.set(controles);
          this.error.set(null);
        },
        error: () => this.error.set('No se pudo leer la bandeja. Revisá que el backend esté corriendo.'),
      });
  }

  /** Genera el Excel de la conciliación y lo deja pendiente de revisión. */
  enviar(taskId: string, envio: EnvioRevision): Observable<Revision> {
    this.fijarAutor(envio.enviado_por);
    return this.http
      .post<Revision>(`${this.apiUrl}/${taskId}`, envio)
      .pipe(tap((nueva) => this.revisiones.update((lista) => [nueva, ...lista])));
  }

  cambiarEstado(id: string, estado: EstadoRevision, comentario = ''): Observable<boolean> {
    return this.accion(id, `${this.apiUrl}/${id}/estado`, { estado, comentario, autor: this.autor() });
  }

  marcarControl(id: string, control: string, ok: boolean): Observable<boolean> {
    return this.accion(id, `${this.apiUrl}/${id}/controles/${control}`, { ok, autor: this.autor() });
  }

  observar(id: string, texto: string, origen: OrigenObservacion): Observable<boolean> {
    return this.accion(id, `${this.apiUrl}/${id}/observaciones`, { texto, origen, autor: this.autor() });
  }

  resolver(id: string, observacionId: string, resuelta: boolean): Observable<boolean> {
    return this.accion(id, `${this.apiUrl}/${id}/observaciones/${observacionId}`, { resuelta, autor: this.autor() });
  }

  eliminar(id: string): void {
    this.http.delete(`${this.apiUrl}/${id}`).subscribe({
      next: () => this.revisiones.update((lista) => lista.filter((r) => r.id !== id)),
      error: () => this.error.set('No se pudo eliminar la revisión.'),
    });
  }

  descargar(revision: Revision): void {
    this.http
      .get(`${this.apiUrl}/${revision.id}/excel`, { responseType: 'blob' })
      .subscribe({
        next: (blob) => this.conciliacionService.descargar({ blob, nombre: revision.archivo }),
        error: () => this.error.set('No se encontró el Excel de esta revisión.'),
      });
  }

  fijarAutor(nombre: string): void {
    const limpio = nombre.trim();
    this.autor.set(limpio);
    try {
      localStorage.setItem(CLAVE_AUTOR, limpio);
    } catch {
      // Sin almacenamiento: se vuelve a pedir el nombre la próxima vez.
    }
  }

  private accion(id: string, url: string, cuerpo: object): Observable<boolean> {
    this.ocupada.set(id);
    this.error.set(null);
    return this.http.post<Revision>(url, cuerpo).pipe(
      map((revision) => {
        this.revisiones.update((lista) => lista.map((r) => (r.id === id ? revision : r)));
        return true;
      }),
      catchError((error: unknown) => {
        const detalle = error instanceof HttpErrorResponse && typeof error.error?.detail === 'string' ? error.error.detail : null;
        this.error.set(detalle ?? 'No se pudo guardar. Revisá que el backend esté corriendo.');
        return of(false);
      }),
      finalize(() => this.ocupada.set(null)),
    );
  }

  private leerAutor(): string {
    try {
      return typeof localStorage === 'undefined' ? '' : (localStorage.getItem(CLAVE_AUTOR) ?? '');
    } catch {
      return '';
    }
  }
}
