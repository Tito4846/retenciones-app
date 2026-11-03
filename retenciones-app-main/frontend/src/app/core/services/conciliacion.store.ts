import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { Observable, Subscription, finalize } from 'rxjs';
import {
  ConciliacionEstado,
  ConciliacionResultado,
  DetalleConciliacion,
  ImpuestoConciliado,
  PasoAutomatico,
  ResumenConciliacion,
  Sugerencia,
} from '../models/conciliacion.models';
import { ConsultaArca } from '../models/conexiones.models';
import { ConciliacionService } from './conciliacion.service';

export type Fase = 'inicial' | ConciliacionEstado['fase'];

/**
 * Estado compartido entre "Importar archivos" y "Conciliación":
 * la carga sigue viva aunque el usuario cambie de pantalla.
 */
@Injectable({ providedIn: 'root' })
export class ConciliacionStore {
  private readonly conciliacionService = inject(ConciliacionService);
  private ejecucion?: Subscription;

  readonly archivoArca = signal<File | null>(null);
  readonly archivoMayor = signal<File | null>(null);

  readonly fase = signal<Fase>('inicial');
  readonly progreso = signal(0);
  readonly error = signal<string | null>(null);

  /** De dónde salió la conciliación en curso: el flujo automático muestra sus pasos. */
  readonly origen = signal<'archivos' | 'conexiones' | 'automatico' | null>(null);
  /** Paso del flujo automático y lo que está haciendo el bot. */
  readonly paso = signal<PasoAutomatico | null>(null);
  readonly detallePaso = signal<string | null>(null);

  /** task_id del backend: con él se pide el Excel de la conciliación. */
  readonly taskId = signal<string | null>(null);
  readonly exportando = signal(false);
  readonly errorExportacion = signal<string | null>(null);

  readonly resumen = signal<ResumenConciliacion | null>(null);
  readonly detalle = signal<DetalleConciliacion | null>(null);
  readonly porImpuesto = signal<ImpuestoConciliado[]>([]);
  readonly sugerencias = signal<Sugerencia[]>([]);
  /** Si los dos archivos no son del mismo mes, impuesto o empresa: qué volver a bajar. */
  readonly avisos = signal<string[]>([]);

  /** Un par a mano en curso (id de la retención de ARCA) y su error. */
  readonly emparejando = signal<string | null>(null);
  readonly errorPar = signal<string | null>(null);

  readonly ocupado = computed(() => this.fase() === 'subiendo' || this.fase() === 'procesando');
  readonly puedeExportar = computed(
    () => this.fase() === 'completado' && !!this.taskId() && !this.exportando(),
  );
  readonly puedeConciliar = computed(
    () => !!this.archivoArca() && !!this.archivoMayor() && !this.ocupado(),
  );

  constructor() {
    inject(DestroyRef).onDestroy(() => this.ejecucion?.unsubscribe());
  }

  conciliar(): void {
    const arca = this.archivoArca();
    const mayor = this.archivoMayor();
    if (!arca || !mayor) return;

    this.ejecutar(this.conciliacionService.conciliar(arca, mayor), 'archivos');
  }

  /** Concilia con la exportación de ARCA y el mayor de Xubio que trajeron los bots. */
  conciliarDesdeConexiones(): void {
    if (this.ocupado()) return;
    this.ejecutar(this.conciliacionService.conciliarDesdeConexiones(), 'conexiones');
  }

  /** Los bots traen el Libro Mayor y las retenciones del período, y se cruzan. */
  conciliarAutomatico(consulta: ConsultaArca): void {
    if (this.ocupado()) return;
    this.ejecutar(this.conciliacionService.conciliarAutomatico(consulta), 'automatico');
  }

  private ejecutar(
    estados: Observable<ConciliacionEstado>,
    origen: 'archivos' | 'conexiones' | 'automatico',
  ): void {
    this.ejecucion?.unsubscribe();
    this.reiniciarResultado();
    this.origen.set(origen);
    this.ejecucion = estados.subscribe((estado) => this.aplicarEstado(estado));
  }

  exportar(): void {
    const taskId = this.taskId();
    if (!taskId || this.exportando()) return;

    this.exportando.set(true);
    this.errorExportacion.set(null);
    this.conciliacionService
      .exportar(taskId)
      .pipe(finalize(() => this.exportando.set(false)))
      .subscribe({
        next: (archivo) => this.conciliacionService.descargar(archivo),
        error: (error: unknown) =>
          this.errorExportacion.set(
            error instanceof HttpErrorResponse && error.status === 404
              ? 'La conciliación venció en el servidor. Volvé a subir los archivos.'
              : 'No se pudo generar el Excel. Revisá que el backend esté corriendo.',
          ),
      });
  }

  /** Concilia a mano una retención de ARCA (no declarada) con un asiento sin certificado. */
  emparejar(arcaId: string, mayorId: string): void {
    const taskId = this.taskId();
    if (!taskId || this.emparejando()) return;
    this.ejecutarPar(arcaId, this.conciliacionService.emparejar(taskId, arcaId, mayorId));
  }

  /** Deshace un par hecho a mano. */
  desemparejar(arcaId: string): void {
    const taskId = this.taskId();
    if (!taskId || this.emparejando()) return;
    this.ejecutarPar(arcaId, this.conciliacionService.desemparejar(taskId, arcaId));
  }

  private ejecutarPar(arcaId: string, pedido: Observable<ConciliacionResultado>): void {
    this.emparejando.set(arcaId);
    this.errorPar.set(null);
    pedido.pipe(finalize(() => this.emparejando.set(null))).subscribe({
      next: (resultado) => this.aplicarResultado(resultado),
      error: (error: unknown) =>
        this.errorPar.set(
          error instanceof HttpErrorResponse && typeof error.error?.detail === 'string'
            ? error.error.detail
            : 'No se pudo guardar el par. Revisá que el backend esté corriendo.',
        ),
    });
  }

  private aplicarResultado(resultado: ConciliacionResultado): void {
    this.resumen.set(resultado.resumen);
    this.detalle.set(resultado.detalle);
    this.porImpuesto.set(resultado.por_impuesto ?? []);
    this.sugerencias.set(resultado.sugerencias ?? []);
    this.avisos.set(resultado.avisos ?? []);
  }

  /** Descarta archivos y resultados para empezar una nueva conciliación. */
  limpiar(): void {
    this.ejecucion?.unsubscribe();
    this.archivoArca.set(null);
    this.archivoMayor.set(null);
    this.fase.set('inicial');
    this.reiniciarResultado();
  }

  private aplicarEstado(estado: ConciliacionEstado): void {
    this.fase.set(estado.fase);

    switch (estado.fase) {
      case 'subiendo':
        this.progreso.set(estado.progreso);
        break;
      case 'procesando':
        this.taskId.set(estado.taskId);
        if (estado.paso) this.paso.set(estado.paso);
        if (estado.detalle) this.detallePaso.set(estado.detalle);
        break;
      case 'completado':
        this.taskId.set(estado.taskId);
        this.aplicarResultado(estado.resultado);
        break;
      case 'error':
        this.error.set(estado.mensaje);
        break;
    }
  }

  private reiniciarResultado(): void {
    this.progreso.set(0);
    this.origen.set(null);
    this.paso.set(null);
    this.detallePaso.set(null);
    this.error.set(null);
    this.taskId.set(null);
    this.errorExportacion.set(null);
    this.resumen.set(null);
    this.detalle.set(null);
    this.porImpuesto.set([]);
    this.sugerencias.set([]);
    this.avisos.set([]);
    this.errorPar.set(null);
  }
}
