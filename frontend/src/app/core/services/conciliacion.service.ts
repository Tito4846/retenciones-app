import {
  HttpClient,
  HttpErrorResponse,
  HttpEvent,
  HttpEventType,
  HttpResponse,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  Observable,
  catchError,
  concat,
  filter,
  map,
  of,
  switchMap,
  takeWhile,
  timer,
} from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  ArchivoExportado,
  ConciliacionEstado,
  ConciliacionResultado,
  EstadoTareaResponse,
  TareaCreadaResponse,
} from '../models/conciliacion.models';
import { ConsultaArca } from '../models/conexiones.models';

const POLLING_INTERVAL_MS = 2000;

@Injectable({ providedIn: 'root' })
export class ConciliacionService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = environment.apiUrl;

  /**
   * Sube los archivos de ARCA y del Libro Mayor de Xubio, emite el progreso de carga
   * y luego consulta el estado de la tarea cada 2s hasta que el backend termine.
   */
  conciliar(archivoArca: File, archivoMayor: File): Observable<ConciliacionEstado> {
    const formData = new FormData();
    formData.append('archivo_arca', archivoArca);
    formData.append('archivo_mayor', archivoMayor);

    return this.http
      .post<TareaCreadaResponse>(`${this.apiUrl}/conciliar`, formData, {
        reportProgress: true,
        observe: 'events',
      })
      .pipe(
        filter(
          (event) =>
            event.type === HttpEventType.UploadProgress || event.type === HttpEventType.Response,
        ),
        switchMap((event) => this.mapearEventoSubida(event)),
        catchError((error: unknown) => of(this.aEstadoError(error))),
      );
  }

  /**
   * Concilia con lo que trajeron los bots de Conexiones (la exportación de ARCA
   * y el Libro Mayor de Xubio). No hay subida: va directo al polling.
   */
  conciliarDesdeConexiones(): Observable<ConciliacionEstado> {
    return this.seguirTarea(this.http.post<TareaCreadaResponse>(`${this.apiUrl}/conexiones/conciliar`, {}));
  }

  /**
   * Todo en un paso: el bot de Xubio baja el Libro Mayor, el de ARCA consulta
   * Mis Retenciones del mismo período y se cruzan. El avance llega por paso.
   */
  conciliarAutomatico(consulta: ConsultaArca): Observable<ConciliacionEstado> {
    return this.seguirTarea(
      this.http.post<TareaCreadaResponse>(`${this.apiUrl}/conexiones/automatico`, consulta),
    );
  }

  private seguirTarea(creada: Observable<TareaCreadaResponse>): Observable<ConciliacionEstado> {
    return creada.pipe(
      switchMap(({ task_id }) =>
        concat(
          of<ConciliacionEstado>({ fase: 'procesando', taskId: task_id }),
          this.consultarEstado(task_id),
        ),
      ),
      catchError((error: unknown) => of(this.aEstadoError(error))),
    );
  }

  private mapearEventoSubida(
    event: HttpEvent<TareaCreadaResponse>,
  ): Observable<ConciliacionEstado> {
    if (event.type === HttpEventType.UploadProgress) {
      const progreso = event.total ? Math.round((100 * event.loaded) / event.total) : 0;
      return of({ fase: 'subiendo', progreso });
    }

    if (event.type === HttpEventType.Response && event.body) {
      const { task_id } = event.body;
      return concat(
        of<ConciliacionEstado>({ fase: 'subiendo', progreso: 100 }),
        of<ConciliacionEstado>({ fase: 'procesando', taskId: task_id }),
        this.consultarEstado(task_id),
      );
    }

    return of({ fase: 'error', mensaje: 'Respuesta inesperada del servidor.' });
  }

  /** Polling a /estado/{task_id} hasta que la tarea finalice (completado o error). */
  private consultarEstado(taskId: string): Observable<ConciliacionEstado> {
    return timer(POLLING_INTERVAL_MS, POLLING_INTERVAL_MS).pipe(
      // switchMap descarta una consulta lenta si llega el siguiente tick.
      switchMap(() => this.http.get<EstadoTareaResponse>(`${this.apiUrl}/estado/${taskId}`)),
      // `true` emite también el valor que corta el polling.
      takeWhile((res) => res.status === 'pendiente' || res.status === 'procesando', true),
      map((res): ConciliacionEstado => {
        switch (res.status) {
          case 'completado':
            return { fase: 'completado', taskId, resultado: res };
          case 'error':
            return { fase: 'error', mensaje: res.mensaje ?? 'El procesamiento falló.' };
          default:
            return { fase: 'procesando', taskId, paso: res.paso, detalle: res.detalle_paso };
        }
      }),
    );
  }

  /** Concilia a mano una retención de ARCA con un asiento del mayor; devuelve el cruce rehecho. */
  emparejar(taskId: string, arcaId: string, mayorId: string): Observable<ConciliacionResultado> {
    return this.http.post<ConciliacionResultado>(`${this.apiUrl}/conciliacion/${taskId}/pares`, {
      arca_id: arcaId,
      mayor_id: mayorId,
    });
  }

  /** Deshace un par hecho a mano. */
  desemparejar(taskId: string, arcaId: string): Observable<ConciliacionResultado> {
    return this.http.delete<ConciliacionResultado>(`${this.apiUrl}/conciliacion/${taskId}/pares/${arcaId}`);
  }

  /** Pide el papel de trabajo .xlsx de una conciliación terminada. */
  exportar(taskId: string): Observable<ArchivoExportado> {
    return this.http
      .get(`${this.apiUrl}/exportar/${taskId}`, { responseType: 'blob', observe: 'response' })
      .pipe(map((respuesta) => ({ blob: respuesta.body!, nombre: this.nombreDesde(respuesta) })));
  }

  /**
   * Guarda el blob en la carpeta de descargas. Va con un <a> temporal: abrir
   * una pestaña con el blob no respeta el nombre del archivo.
   */
  descargar({ blob, nombre }: ArchivoExportado): void {
    const url = URL.createObjectURL(blob);
    const enlace = document.createElement('a');
    enlace.href = url;
    enlace.download = nombre;
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    // Se revoca un rato después del click: revocarla en el mismo tick puede
    // cortar la descarga (la baja vacía o falla en algunos navegadores).
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private nombreDesde(respuesta: HttpResponse<Blob>): string {
    const disposicion = respuesta.headers.get('content-disposition') ?? '';
    // filename*=UTF-8'' primero: con acentos, el filename= a secas llega roto.
    const extendido = disposicion.match(/filename\*=UTF-8''([^;]+)/i);
    if (extendido) {
      try {
        return decodeURIComponent(extendido[1]);
      } catch {
        // Cae al filename simple.
      }
    }
    return disposicion.match(/filename="?([^";]+)"?/i)?.[1] ?? 'Retenciones ARCA.xlsx';
  }

  private aEstadoError(error: unknown): ConciliacionEstado {
    if (error instanceof HttpErrorResponse) {
      const detalle = typeof error.error?.detail === 'string' ? error.error.detail : error.message;
      return { fase: 'error', mensaje: detalle };
    }
    return { fase: 'error', mensaje: 'Error desconocido.' };
  }
}
