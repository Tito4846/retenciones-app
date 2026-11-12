import {
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  input,
  model,
  signal,
  viewChild,
} from '@angular/core';
import { ImpuestoArca } from '../../core/models/conexiones.models';

interface Opcion {
  codigo: string;
  /** "Sicore-impto.a las ganancias" (sin el código adelante). */
  nombre: string;
  /** Nombre corto: "Ganancias", "IVA"... */
  corto: string;
}

/** Nombres cortos de los impuestos más usados; el resto muestra el de ARCA. */
export const NOMBRES_CORTOS: Record<string, string> = {
  '217': 'Ganancias',
  '767': 'IVA',
  '216': 'SIRE IVA',
  '219': 'Bienes Personales',
  '210': 'Ganancias RG 830',
  '939': 'Impuesto PAÍS',
  '172': 'ITI',
  '787': 'Ganancias 4ta categoría',
  '218': 'Ganancias exterior',
  '466': 'Premios',
  '222': 'Donaciones',
};

/** Los que casi siempre tienen retenciones: se ofrecen como atajo. */
export const SUGERIDOS = ['217', '767'];

/**
 * Desplegable para elegir el impuesto de la consulta: uno solo, como el combo
 * de ARCA (elegir otro lo reemplaza). Tiene buscador porque la lista es larga
 * y los nombres de ARCA no se leen fácil.
 */
@Component({
  selector: 'app-selector-impuestos',
  templateUrl: './selector-impuestos.html',
  host: {
    '(document:click)': 'alClicAfuera($event)',
    '(document:keydown.escape)': 'cerrar()',
  },
})
export class SelectorImpuestos {
  private readonly elemento = inject(ElementRef<HTMLElement>);
  private readonly injector = inject(Injector);
  private readonly buscador = viewChild<ElementRef<HTMLInputElement>>('buscador');

  readonly impuestos = input.required<ImpuestoArca[]>();
  readonly seleccion = model<string[]>([]);

  protected readonly abierto = signal(false);
  protected readonly filtro = signal('');

  protected readonly opciones = computed<Opcion[]>(() =>
    this.impuestos().map(({ codigo, descripcion }) => {
      // ARCA los manda en mayúsculas: en formato oración se leen mejor.
      const original = descripcion.replace(new RegExp(`^${codigo}\\s*-\\s*`), '').trim();
      const nombre = original.charAt(0) + original.slice(1).toLowerCase();
      return { codigo, nombre, corto: NOMBRES_CORTOS[codigo] ?? nombre };
    }),
  );

  protected readonly visibles = computed(() => {
    const texto = this.filtro().trim().toLowerCase();
    if (!texto) return this.opciones();
    return this.opciones().filter(
      (o) => o.codigo.includes(texto) || o.nombre.toLowerCase().includes(texto) || o.corto.toLowerCase().includes(texto),
    );
  });

  /** Los elegidos que ARCA ofrece, en el orden en que se eligieron. */
  protected readonly elegidos = computed<Opcion[]>(() => {
    const porCodigo = new Map(this.opciones().map((o) => [o.codigo, o]));
    return this.seleccion()
      .map((codigo) => porCodigo.get(codigo))
      .filter((o): o is Opcion => !!o);
  });

  protected readonly sugeridos = computed(() => this.opciones().filter((o) => SUGERIDOS.includes(o.codigo)));

  protected estaElegido(codigo: string): boolean {
    return this.seleccion().includes(codigo);
  }

  protected alternar(): void {
    this.abierto.update((abierto) => !abierto);
    if (!this.abierto()) {
      this.filtro.set('');
      return;
    }
    // `autofocus` no corre en lo que se agrega después de cargar la página:
    // el buscador recibe el foco cuando la lista ya está dibujada.
    afterNextRender(() => this.buscador()?.nativeElement.focus(), { injector: this.injector });
  }

  cerrar(): void {
    this.abierto.set(false);
    this.filtro.set('');
  }

  /**
   * Por el camino del evento y no por `contains`: al marcar o desmarcar, el
   * tilde que se tocó se redibuja antes de que llegue el clic al documento, y
   * ese elemento ya no está dentro del selector.
   */
  protected alClicAfuera(event: MouseEvent): void {
    if (this.abierto() && !event.composedPath().includes(this.elemento.nativeElement)) this.cerrar();
  }

  /** Como en ARCA, un impuesto por consulta: elegir otro reemplaza al anterior. */
  protected cambiar(codigo: string): void {
    this.seleccion.set([codigo]);
    this.cerrar();
  }

  protected quitar(codigo: string, event: Event): void {
    event.stopPropagation();
    this.seleccion.update((actual) => actual.filter((c) => c !== codigo));
  }

  /** Enter en el buscador elige el primero que coincide. */
  protected elegirPrimero(event: Event): void {
    event.preventDefault();
    const primero = this.visibles()[0];
    if (primero) this.cambiar(primero.codigo);
  }
}
