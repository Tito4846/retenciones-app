import { Injectable, computed, inject, linkedSignal, signal, untracked } from '@angular/core';
import { DetalleConciliacion, MovimientoNuevo, Retencion, ResumenConciliacion } from '../../core/models/conciliacion.models';
import { ConciliacionStore } from '../../core/services/conciliacion.store';

export type Bandeja = keyof DetalleConciliacion;
export type Pestana = Bandeja | 'revisar';
export type CampoEditable = 'fecha' | 'razon_social' | 'certificado' | 'documento' | 'importe';

/**
 * Dónde arranca la tabla: en lo que hay que resolver, nunca en lo que ya cerró.
 * Primero lo que falta de cada lado (las tarjetas C y B), después los pares dudosos
 * y solo si no queda nada, los conciliados.
 */
export function pestanaInicial(resumen: Pick<ResumenConciliacion, 'total_faltan_en_mayor' | 'total_faltan_en_arca'> | null, sugerencias: number): Pestana {
  if (resumen?.total_faltan_en_mayor) return 'faltan_en_mayor';
  if (resumen?.total_faltan_en_arca) return 'faltan_en_arca';
  if (sugerencias) return 'revisar';
  return 'conciliados';
}

/** "1.234,56" (como se escribe acá) o "1234.56" → número. NaN si no es un importe. */
export function leerImporte(texto: string): number {
  const limpio = texto.trim();
  if (!limpio) return NaN;
  return Number(limpio.includes(',') ? limpio.replace(/\./g, '').replace(',', '.') : limpio);
}

export interface Borrador {
  id: string;
  campo: CampoEditable;
  valor: string;
  error?: boolean;
}

/**
 * El estado de la tabla de la conciliación, sin nada de pantalla: qué pestaña
 * está activa, qué celda se está editando, lo corregido, lo agregado y lo que se
 * quita al guardar. Se provee por pantalla (`providers`): se vacía al salir.
 */
@Injectable()
export class EstadoTablaConciliacion {
  private readonly store = inject(ConciliacionStore);

  /**
   * La conciliación que se está mirando. Va en un computed aparte a propósito:
   * solo avisa cuando cambia de verdad, y no cada vez que guardar o emparejar
   * cambian el resumen (eso volvía a elegir la pestaña).
   */
  private readonly conciliacionActual = computed(() => (this.store.resumen() ? this.store.taskId() : null));

  readonly pestanaActiva = linkedSignal<string | null, Pestana>({
    // Se elige al llegar un resultado nuevo, no al guardar o emparejar dentro del mismo.
    source: this.conciliacionActual,
    computation: () => untracked(() => pestanaInicial(this.store.resumen(), this.store.sugerencias().length)),
  });

  elegirPestana(id: Pestana): void {
    this.pestanaActiva.set(id);
    this.store.errorPar.set(null);
    this.confirmandoQuitar.set(null);
  }

  /** Los avisos de arriba se pueden cerrar; vuelven con la conciliación siguiente. */
  readonly avisosCerrados = linkedSignal({ source: this.conciliacionActual, computation: () => false });

  // --- Edición en línea: se corrige escribiendo sobre la celda --------------

  /** Cambios hechos a mano, por id de fila; se vacían con cada conciliación nueva. */
  readonly ediciones = linkedSignal<string | null, Map<string, Partial<Retencion>>>({
    source: () => this.store.taskId(),
    computation: () => new Map(),
  });
  /** La celda que se está editando (una sola a la vez). */
  readonly borrador = signal<Borrador | null>(null);

  /** La fila con lo editado a mano encima de lo que vino de la conciliación. */
  readonly conEdicion = (f: Retencion): Retencion => {
    const cambios = f.id ? this.ediciones().get(f.id) : undefined;
    return cambios ? { ...f, ...cambios } : f;
  };

  enEdicion(item: Retencion, campo: CampoEditable): boolean {
    const b = this.borrador();
    return !!b && b.id === item.id && b.campo === campo;
  }

  editado(item: Retencion, campo: CampoEditable): boolean {
    return !!item.id && campo in (this.ediciones().get(item.id) ?? {});
  }

  editar(item: Retencion, campo: CampoEditable): void {
    if (!item.id) return;
    const crudo = item[campo];
    const valor =
      campo === 'importe' ? String(crudo ?? '').replace('.', ',') : campo === 'fecha' ? String(crudo ?? '').slice(0, 10) : String(crudo ?? '');
    this.confirmandoQuitar.set(null);
    this.borrador.set({ id: item.id, campo, valor });
  }

  escribir(valor: string): void {
    this.borrador.update((b) => (b ? { ...b, valor, error: false } : b));
  }

  /** Enter o salir de la celda. El importe se lee como se escribe acá: "1.234,56". */
  confirmarBorrador(): void {
    const b = this.borrador();
    if (!b) return;
    const limpio = b.valor.trim();
    let valor: string | number = limpio;
    if (b.campo === 'importe') {
      valor = leerImporte(limpio);
      if (!Number.isFinite(valor) || valor < 0) return this.borrador.set({ ...b, error: true });
    } else if (b.campo === 'fecha' && !/^\d{4}-\d{2}-\d{2}$/.test(limpio)) {
      return this.borrador.set({ ...b, error: true });
    }
    const original = this.original(b.id);
    this.ediciones.update((m) => {
      const nueva = new Map(m);
      const cambios = { ...nueva.get(b.id) };
      const igual = b.campo === 'fecha' ? String(original?.fecha ?? '').slice(0, 10) === valor : (original?.[b.campo] ?? '') === valor;
      if (igual) delete cambios[b.campo];
      else Object.assign(cambios, { [b.campo]: valor });
      if (Object.keys(cambios).length) nueva.set(b.id, cambios);
      else nueva.delete(b.id);
      return nueva;
    });
    this.borrador.set(null);
  }

  cancelarBorrador(): void {
    this.borrador.set(null);
  }

  // --- Agregados y quitados (se mandan al guardar) ------------------------------

  /** Agregados que todavía no se guardaron. */
  readonly nuevos = linkedSignal<string | null, MovimientoNuevo[]>({ source: () => this.store.taskId(), computation: () => [] });
  /** Ids marcados para quitar al guardar. */
  readonly eliminados = linkedSignal<string | null, Set<string>>({ source: () => this.store.taskId(), computation: () => new Set() });
  /** La fila que está pidiendo confirmación para quitarse (el popconfirm abierto). */
  readonly confirmandoQuitar = signal<string | null>(null);

  readonly pendientes = computed(() => this.ediciones().size + this.nuevos().length + this.eliminados().size);

  eliminado(item: Retencion): boolean {
    return !!item.id && this.eliminados().has(item.id);
  }

  /** Marca o desmarca la fila para quitarla al guardar. */
  alternarEliminado(item: Retencion): void {
    if (!item.id) return;
    this.confirmandoQuitar.set(null);
    this.eliminados.update((s) => {
      const nueva = new Set(s);
      if (!nueva.delete(item.id!)) nueva.add(item.id!);
      return nueva;
    });
  }

  deshacerTodo(): void {
    this.borrador.set(null);
    this.ediciones.set(new Map());
    this.nuevos.set([]);
    this.eliminados.set(new Set());
  }

  private original(id: string): Retencion | undefined {
    const d = this.store.detalle();
    return d ? [...d.conciliados, ...d.faltan_en_arca, ...d.faltan_en_mayor].find((f) => f.id === id) : undefined;
  }
}
