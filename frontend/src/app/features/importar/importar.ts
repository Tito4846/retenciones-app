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
import { Router } from '@angular/router';
import { ConciliacionStore } from '../../core/services/conciliacion.store';
import { ConexionesStore } from '../../core/services/conexiones.store';
import { ConsultaArca } from './consulta-arca';

type TipoArchivo = 'arca' | 'mayor';

interface ZonaCarga {
  tipo: TipoArchivo;
  titulo: string;
  ayuda: string;
  accept: string;
  archivo: File | null;
}

@Component({
  selector: 'app-importar',
  imports: [DecimalPipe, ConsultaArca],
  templateUrl: './importar.html',
})
export class Importar {
  protected readonly store = inject(ConciliacionStore);
  protected readonly conexiones = inject(ConexionesStore);
  private readonly router = inject(Router);

  protected readonly zonas = computed<ZonaCarga[]>(() => [
    {
      tipo: 'arca',
      titulo: 'Retenciones ARCA',
      ayuda: 'Mis Retenciones · CSV, XLSX o TXT',
      accept: '.csv,.xlsx,.xls,.txt',
      archivo: this.store.archivoArca(),
    },
    {
      tipo: 'mayor',
      titulo: 'Libro Mayor Xubio',
      ayuda: 'Exportación del mayor · CSV o XLSX',
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

    // Al terminar el cruce, llevar al usuario a ver los resultados.
    effect(() => {
      if (this.esperandoResultado() && this.store.fase() === 'completado') {
        this.router.navigate(['/conciliacion']);
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
