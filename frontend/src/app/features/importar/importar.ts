import { DecimalPipe } from '@angular/common';
import {
  Component,
  DestroyRef,
  WritableSignal,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { ConciliacionStore } from '../../core/services/conciliacion.store';
import { LotesStore } from '../../core/services/lotes.store';
import { ConexionesStore } from '../../core/services/conexiones.store';
import { Ayuda } from '../../core/ui/ayuda.directive';
import { ConsultaArca } from './consulta-arca';

type TipoArchivo = 'arca' | 'mayor';

interface ZonaCarga {
  tipo: TipoArchivo;
  titulo: string;
  /** Qué archivo va: en el tooltip de la zona. */
  ayuda: string;
  formatos: string;
  accept: string;
  archivo: File | null;
}

@Component({
  selector: 'app-importar',
  imports: [Ayuda, ConsultaArca, DecimalPipe],
  templateUrl: './importar.html',
})
export class Importar {
  protected readonly store = inject(ConciliacionStore);
  protected readonly conexiones = inject(ConexionesStore);
  private readonly lotes = inject(LotesStore);

  protected readonly zonas = computed<ZonaCarga[]>(() => [
    {
      tipo: 'arca',
      titulo: 'Excel de ARCA · Mis Retenciones',
      ayuda: 'Mis Retenciones filtrado por la CUIT, el impuesto y las fechas.',
      formatos: 'CSV · XLSX · TXT',
      accept: '.csv,.xlsx,.xls,.txt',
      archivo: this.store.archivoArca(),
    },
    {
      tipo: 'mayor',
      titulo: 'Excel de Xubio · Libro Mayor',
      ayuda: 'El Libro Mayor de la cuenta de retenciones de ese impuesto, mismas fechas.',
      formatos: 'CSV · XLSX',
      accept: '.csv,.xlsx,.xls',
      archivo: this.store.archivoMayor(),
    },
  ]);

  /** Zona de arrastre que tiene un archivo encima (para resaltarla). */
  protected readonly arrastrando = signal<TipoArchivo | null>(null);

  /** Solo redirigimos si la conciliación se inició desde esta pantalla. */
  private readonly esperandoResultado = signal(false);

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => this.conexiones.seguir(destroyRef));

    // Al terminar el cruce, el lote se arma solo y se abre en Verificación.
    effect(() => {
      const taskId = this.store.taskId();
      if (this.esperandoResultado() && this.store.fase() === 'completado' && taskId) {
        this.esperandoResultado.set(false);
        this.lotes.aVerificacion(taskId);
      }
    });
  }

  protected conciliar(): void {
    this.esperandoResultado.set(true);
    this.store.conciliar();
  }

  protected conciliarDesdeConexiones(): void {
    this.esperandoResultado.set(true);
    this.store.conciliarDesdeConexiones();
  }

  protected onArchivoSeleccionado(event: Event, tipo: TipoArchivo): void {
    const input = event.target as HTMLInputElement;
    this.archivoDe(tipo).set(input.files?.item(0) ?? null);
    input.value = '';
  }

  protected onDragOver(event: DragEvent, tipo: TipoArchivo): void {
    event.preventDefault();
    this.arrastrando.set(tipo);
  }

  protected onDrop(event: DragEvent, tipo: TipoArchivo): void {
    event.preventDefault();
    this.arrastrando.set(null);
    const archivo = event.dataTransfer?.files.item(0);
    if (archivo) this.archivoDe(tipo).set(archivo);
  }

  protected quitar(tipo: TipoArchivo): void {
    this.archivoDe(tipo).set(null);
  }

  private archivoDe(tipo: TipoArchivo): WritableSignal<File | null> {
    return tipo === 'arca' ? this.store.archivoArca : this.store.archivoMayor;
  }
}
