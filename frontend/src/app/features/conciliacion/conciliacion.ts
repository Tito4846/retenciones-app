import { CurrencyPipe, DatePipe, NgTemplateOutlet, PercentPipe } from '@angular/common';
import { Component, ElementRef, computed, effect, inject, linkedSignal, signal, untracked, viewChild } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import {
  CambiosMovimientos,
  CriterioConciliacion,
  MovimientoNuevo,
  DetalleConciliacion,
  MotivoDiferencia,
  Retencion,
  Sugerencia,
} from '../../core/models/conciliacion.models';
import { ConciliacionStore } from '../../core/services/conciliacion.store';
import { EnvioRevision, Revision } from '../../core/models/revision.models';
import { RevisionesStore } from '../../core/services/revisiones.store';

type Bandeja = keyof DetalleConciliacion;
type Pestana = Bandeja | 'revisar';
type CampoOrden = 'fecha' | 'razon_social' | 'importe' | 'impuesto';
type CampoEditable = 'fecha' | 'razon_social' | 'certificado' | 'documento' | 'importe';

interface PestanaConfig {
  id: Pestana;
  /** A, B o C (la categoría de la conciliación); "Para revisar" no tiene. */
  letra?: 'A' | 'B' | 'C';
  titulo: string;
  /** Qué significa para el contador y qué hay que hacer. */
  significado: string;
  accion: string;
  vacio: string;
  tono: 'rojo' | 'ambar' | 'verde' | 'marca';
}

const ETIQUETAS_CRITERIO: Record<CriterioConciliacion, string> = {
  comprobante: 'Nro. de comprobante',
  cuit_importe: 'CUIT + importe',
  razon_social: 'Razón social + importe',
  importe_fecha: 'Importe + fecha',
  diferencia_menor: 'Mismo agente, centavos de diferencia',
  manual: 'A mano',
};

const PESTANAS: readonly PestanaConfig[] = [
  {
    id: 'conciliados',
    letra: 'A',
    titulo: 'Conciliados',
    significado: 'Coinciden en ARCA y en el Libro Mayor de Xubio',
    accion: 'Sin acciones pendientes',
    vacio: 'Todavía no hay retenciones conciliadas.',
    tono: 'verde',
  },
  {
    id: 'faltan_en_arca',
    letra: 'B',
    titulo: 'Faltan en ARCA',
    significado: 'Registradas en el Libro Mayor de Xubio, pero no figuran en ARCA',
    accion: 'Reclamar el certificado al agente o revisar el asiento',
    vacio: 'Todo lo registrado en el Libro Mayor de Xubio figura en ARCA.',
    tono: 'ambar',
  },
  {
    id: 'faltan_en_mayor',
    letra: 'C',
    titulo: 'Faltan en el Mayor',
    significado: 'Declaradas en ARCA, sin registrar en el Libro Mayor de Xubio',
    accion: 'Registrar el asiento en Xubio (ajuste contable)',
    vacio: 'Todas las retenciones de ARCA están registradas en el Libro Mayor de Xubio.',
    tono: 'rojo',
  },
  {
    id: 'revisar',
    titulo: 'Para revisar',
    significado: 'Posibles pares que el cruce no confirmó solo',
    accion: 'Confirmalos o descartalos',
    vacio: 'No hay pares dudosos para revisar.',
    tono: 'marca',
  },
];

/** Casos de B y C que se muestran de entrada (el detalle completo está en cada pestaña y en el Excel). */
const CASOS_EN_RESUMEN = 10;

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const PESOS = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS' });

/** "2026-10-16" → "16/10/2026". */
function fechaCorta(iso: string | null): string {
  if (!iso) return 's/f';
  const [a, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${a}`;
}

/** "octubre de 2026" (o "septiembre y octubre de 2026") según las fechas de los movimientos. */
function periodoDe(movs: Retencion[]): string {
  const meses = [...new Set(movs.map((m) => m.fecha?.slice(0, 7)).filter((m): m is string => !!m))].sort();
  if (!meses.length) return 'el período';
  const nombres = meses.map((m) => `${MESES[Number(m.slice(5, 7)) - 1]} de ${m.slice(0, 4)}`);
  return nombres.length === 1 ? nombres[0] : `${nombres.slice(0, -1).join(', ')} y ${nombres.at(-1)}`;
}

/** El mensaje para el cliente: lo registrado en el mayor que ARCA no informa. */
function armarAviso(movs: Retencion[]): string {
  const total = movs.reduce((s, m) => s + m.importe, 0);
  const lineas = movs.map((m) => {
    const tipo = m.tipo === 'Percepcion' ? 'percepción' : 'retención';
    const impuesto = m.impuesto ? `${tipo} de ${m.impuesto}` : tipo;
    const comprobante = m.documento ? ` · ${m.documento}` : '';
    return `• ${fechaCorta(m.fecha)} · ${m.razon_social || 'Sin organización'} · ${impuesto}${comprobante} · ${PESOS.format(m.importe)}`;
  });
  return [
    'Hola, ¿cómo estás?',
    '',
    `Al conciliar las retenciones y percepciones de ${periodoDe(movs)} encontramos ${
      movs.length === 1 ? 'un movimiento registrado' : `${movs.length} movimientos registrados`
    } en la contabilidad que no ${movs.length === 1 ? 'figura' : 'figuran'} en ARCA (Mis Retenciones):`,
    '',
    ...lineas,
    '',
    `Total: ${PESOS.format(total)}`,
    '',
    'Mientras el agente no lo informe en ARCA no se puede computar en la declaración jurada. Te pedimos que:',
    '1. Nos confirmes si estos importes efectivamente te los retuvieron o percibieron (o si se trata de otro concepto).',
    '2. Si corresponde, le pidas al agente el certificado o que rectifique su declaración.',
    '',
    'Cualquier duda, quedamos a disposición.',
    'Saludos.',
  ].join('\n');
}

function normalizar(texto: string | null | undefined): string {
  return (texto ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

@Component({
  selector: 'app-conciliacion',
  imports: [CurrencyPipe, DatePipe, NgTemplateOutlet, PercentPipe, RouterLink],
  templateUrl: './conciliacion.html',
  // El embudo está fijo en la pantalla: si la página se mueve, se mueve con su columna.
  host: { '(window:scroll)': 'ubicarFiltro()', '(window:resize)': 'ubicarFiltro()' },
})
export class Conciliacion {
  protected readonly store = inject(ConciliacionStore);
  private readonly router = inject(Router);

  protected readonly pestanas = PESTANAS;
  protected readonly casosEnResumen = CASOS_EN_RESUMEN;

  /** Los primeros casos de B y C: lo que hay que reclamar y lo que hay que ajustar. */
  protected readonly primerosCasos = computed(() => {
    const d = this.store.detalle();
    if (!d) return [];
    return (['faltan_en_arca', 'faltan_en_mayor'] as const).map((id) => ({
      config: PESTANAS.find((p) => p.id === id)!,
      filas: d[id].slice(0, CASOS_EN_RESUMEN).map(this.conEdicion),
      total: d[id].length,
    }));
  });

  /** Sugerencias que la persona descartó en esta pantalla (no se mandan al servidor). */
  private readonly descartadas = signal<Set<string>>(new Set());
  protected readonly sugerencias = computed(() =>
    this.store.sugerencias().filter((s) => !this.descartadas().has(`${s.id_arca}|${s.id_mayor}`)),
  );

  /**
   * La conciliación que se está mirando. Va en un computed aparte a propósito:
   * solo avisa cuando cambia de verdad, y no cada vez que guardar o emparejar
   * cambian el resumen (eso volvía a elegir la pestaña).
   */
  private readonly conciliacionActual = computed(() => (this.store.resumen() ? this.store.taskId() : null));

  /** Arranca donde hay trabajo: lo dudoso, lo no declarado, lo sin certificado. */
  protected readonly pestanaActiva = linkedSignal<string | null, Pestana>({
    // Se elige al llegar un resultado nuevo, no al guardar o emparejar dentro del mismo.
    source: this.conciliacionActual,
    computation: () =>
      untracked(() => {
        if (this.store.sugerencias().length) return 'revisar';
        const r = this.store.resumen();
        if (r?.total_faltan_en_mayor) return 'faltan_en_mayor';
        if (r?.total_faltan_en_arca) return 'faltan_en_arca';
        return 'conciliados';
      }),
  });
  protected readonly config = computed(
    () => PESTANAS.find((p) => p.id === this.pestanaActiva()) ?? PESTANAS[0],
  );

  /** Por qué la diferencia ARCA − Mayor no da 0 (vacío si da 0). */
  protected readonly motivosDiferencia = computed(() => this.store.resumen()?.motivos_diferencia ?? []);
  /** Las filas responsables de la diferencia: van en rojo en el detalle. */
  private readonly responsables = computed(
    () => new Set(this.motivosDiferencia().flatMap((m) => m.ids)),
  );

  protected esResponsable(item: Retencion): boolean {
    return !!item.id && this.responsables().has(item.id);
  }

  /** Lleva al detalle donde están las filas de ese motivo. */
  protected verMotivo(motivo: MotivoDiferencia): void {
    this.elegirPestana(motivo.motivo === 'redondeos' ? 'conciliados' : motivo.motivo);
    document.getElementById('detalle')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // --- Edición en línea: se corrige escribiendo sobre la celda (solo en pantalla y en el aviso)
  /** Cambios hechos a mano, por id de fila; se vacían con cada conciliación nueva. */
  protected readonly ediciones = linkedSignal<string | null, Map<string, Partial<Retencion>>>({
    source: () => this.store.taskId(),
    computation: () => new Map(),
  });
  protected readonly borrador = signal<{ id: string; campo: CampoEditable; valor: string; error?: boolean } | null>(null);
  private readonly celdaEnEdicion = viewChild<ElementRef<HTMLInputElement>>('celdaEnEdicion');

  constructor() {
    // El input aparece recién al editar: se enfoca y se selecciona para escribir encima.
    effect(() => {
      const celda = this.celdaEnEdicion()?.nativeElement;
      if (celda) queueMicrotask(() => (celda.focus(), celda.select?.()));
    });
  }

  /** La fila con lo editado a mano encima de lo que vino de la conciliación. */
  protected conEdicion = (f: Retencion): Retencion => {
    const cambios = f.id ? this.ediciones().get(f.id) : undefined;
    return cambios ? { ...f, ...cambios } : f;
  };

  protected enEdicion(item: Retencion, campo: CampoEditable): boolean {
    const b = this.borrador();
    return !!b && b.id === item.id && b.campo === campo;
  }

  protected editado(item: Retencion, campo: CampoEditable): boolean {
    return !!item.id && campo in (this.ediciones().get(item.id) ?? {});
  }

  protected editar(item: Retencion, campo: CampoEditable): void {
    if (!item.id) return;
    const crudo = item[campo];
    const valor =
      campo === 'importe' ? String(crudo ?? '').replace('.', ',') : campo === 'fecha' ? String(crudo ?? '').slice(0, 10) : String(crudo ?? '');
    this.borrador.set({ id: item.id, campo, valor });
  }

  protected escribir(valor: string): void {
    this.borrador.update((b) => (b ? { ...b, valor, error: false } : b));
  }

  /** Enter o salir de la celda. El importe se lee como se escribe acá: "1.234,56". */
  protected confirmarBorrador(): void {
    const b = this.borrador();
    if (!b) return;
    const limpio = b.valor.trim();
    let valor: string | number = limpio;
    if (b.campo === 'importe') {
      valor = Number(limpio.includes(',') ? limpio.replace(/\./g, '').replace(',', '.') : limpio);
      if (!limpio || !Number.isFinite(valor) || valor < 0) return this.borrador.set({ ...b, error: true });
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

  protected cancelarBorrador(): void {
    this.borrador.set(null);
  }

  protected deshacerEdiciones(): void {
    this.borrador.set(null);
    this.ediciones.set(new Map());
    this.nuevos.set([]);
    this.eliminados.set(new Set());
  }

  // --- Movimientos agregados a mano y guardado en el servidor
  /** Agregados que todavía no se guardaron. */
  protected readonly nuevos = linkedSignal<string | null, MovimientoNuevo[]>({
    source: () => this.store.taskId(),
    computation: () => [],
  });
  /** Ids marcados para quitar al guardar. */
  protected readonly eliminados = linkedSignal<string | null, Set<string>>({
    source: () => this.store.taskId(),
    computation: () => new Set(),
  });
  protected readonly pendientes = computed(() => this.ediciones().size + this.nuevos().length + this.eliminados().size);

  protected eliminado(item: Retencion): boolean {
    return !!item.id && this.eliminados().has(item.id);
  }

  /** Marca o desmarca la fila para quitarla al guardar. */
  protected alternarEliminado(item: Retencion): void {
    if (!item.id) return;
    this.eliminados.update((s) => {
      const nueva = new Set(s);
      if (!nueva.delete(item.id!)) nueva.add(item.id!);
      return nueva;
    });
  }
  protected readonly formNuevo = signal<MovimientoNuevo | null>(null);
  protected readonly errorNuevo = signal<string | null>(null);
  protected readonly guardado = signal(false);

  /** Los impuestos de esta conciliación, para elegir en el alta. */
  protected readonly impuestosConocidos = computed(() => {
    const d = this.store.detalle();
    const todos = d ? [...d.conciliados, ...d.faltan_en_arca, ...d.faltan_en_mayor].map((f) => f.impuesto) : [];
    return [...new Set([...todos.filter(Boolean), 'IVA', 'Ganancias', 'Ingresos Brutos', 'SUSS'])];
  });

  /** Abre el alta: del lado que falta según la pestaña (en «Faltan en el Mayor», un asiento del mayor). */
  protected abrirNuevo(): void {
    this.errorNuevo.set(null);
    this.formNuevo.set({
      lado: this.pestanaActiva() === 'faltan_en_arca' ? 'arca' : 'mayor',
      fecha: '',
      razon_social: '',
      cuit: '',
      impuesto: this.impuestosConocidos()[0] ?? 'IVA',
      tipo: 'Retencion',
      comprobante: '',
      importe: 0,
    });
  }

  protected campoNuevo<K extends keyof MovimientoNuevo>(campo: K, valor: MovimientoNuevo[K]): void {
    this.formNuevo.update((f) => (f ? { ...f, [campo]: valor } : f));
  }

  protected agregarNuevo(importeTexto: string): void {
    const f = this.formNuevo();
    if (!f) return;
    const limpio = importeTexto.trim();
    const importe = Number(limpio.includes(',') ? limpio.replace(/\./g, '').replace(',', '.') : limpio);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.fecha)) return this.errorNuevo.set('Elegí la fecha.');
    if (!f.razon_social.trim()) return this.errorNuevo.set('Escribí el nombre del agente u organización.');
    if (!Number.isFinite(importe) || importe <= 0) return this.errorNuevo.set('Escribí un importe mayor a 0, por ejemplo 1.234,56.');
    this.nuevos.update((n) => [...n, { ...f, razon_social: f.razon_social.trim(), importe }]);
    this.formNuevo.set(null);
  }

  protected quitarNuevo(indice: number): void {
    this.nuevos.update((n) => n.filter((_, i) => i !== indice));
  }

  /** Manda lo editado y lo agregado: el servidor rehace el cruce y el Excel sale con los cambios. */
  protected guardar(): void {
    this.borrador.set(null);
    const cambios: CambiosMovimientos['cambios'] = {};
    for (const [id, campos] of this.ediciones()) {
      cambios[id] = Object.fromEntries(Object.entries(campos).map(([k, v]) => [k, v as string | number]));
    }
    this.store.guardarMovimientos({ cambios, nuevos: this.nuevos(), eliminados: [...this.eliminados()] }).subscribe((ok) => {
      if (!ok) return;
      this.ediciones.set(new Map());
      this.nuevos.set([]);
      this.eliminados.set(new Set());
      this.guardado.set(true);
      setTimeout(() => this.guardado.set(false), 2500);
    });
  }

  private original(id: string): Retencion | undefined {
    const d = this.store.detalle();
    return d ? [...d.conciliados, ...d.faltan_en_arca, ...d.faltan_en_mayor].find((f) => f.id === id) : undefined;
  }

  // --- Filtros y orden de la tabla
  protected readonly busqueda = signal('');
  protected readonly orden = signal<{ campo: CampoOrden; dir: 1 | -1 }>({ campo: 'fecha', dir: 1 });

  /** Embudos al estilo Excel: por columna, los valores marcados (tal como se leen). Se vacían al cambiar de pestaña. */
  protected readonly filtros = linkedSignal<Pestana | null, Partial<Record<CampoOrden, Set<string>>>>({
    source: () => this.pestanaActiva(),
    computation: () => ({}),
  });
  /** Embudo abierto y dónde dibujarlo (fijo en la pantalla, para que el scroll de la tabla no lo corte). */
  protected readonly filtroAbierto = signal<{ campo: CampoOrden; top: number; left: number } | null>(null);
  protected readonly busquedaFiltro = signal('');

  /** El valor de la celda como se ve: es con lo que se filtra. */
  protected valorFiltrable(f: Retencion, campo: CampoOrden): string {
    if (campo === 'fecha') return fechaCorta(f.fecha);
    if (campo === 'importe') return PESOS.format(f.importe);
    if (campo === 'impuesto') return f.impuesto || 'Sin identificar';
    return f.razon_social || '(Vacío)';
  }

  private readonly filasPestana = computed<Retencion[]>(() => {
    const pestana = this.pestanaActiva();
    const d = this.store.detalle();
    return !d || pestana === 'revisar' ? [] : d[pestana].map(this.conEdicion);
  });

  protected readonly filas = computed<Retencion[]>(() => {
    const texto = normalizar(this.busqueda().trim());
    const filtros = Object.entries(this.filtros()).filter(([, v]) => v.size) as [CampoOrden, Set<string>][];
    const { campo, dir } = this.orden();

    return this.filasPestana()
      .filter((f) => filtros.every(([c, valores]) => valores.has(this.valorFiltrable(f, c))))
      .filter(
        (f) =>
          !texto ||
          [f.cuit, f.cuit.replace(/\D/g, ''), f.razon_social, f.certificado, f.documento, f.documento_mayor, f.descripcion, f.cuenta, String(f.importe), PESOS.format(f.importe), fechaCorta(f.fecha)]
            .some((v) => normalizar(v).includes(texto)),
      )
      .sort((a, b) => {
        const va = a[campo] ?? '';
        const vb = b[campo] ?? '';
        return (typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb), 'es')) * dir;
      });
  });

  /** Valores únicos de la columna, en el orden de la columna, filtrados por lo que se tipea en el embudo. */
  protected readonly valoresUnicos = computed(() => {
    const abierto = this.filtroAbierto();
    if (!abierto) return [];
    const campo = abierto.campo;
    const orden = new Map<string, string | number>();
    for (const f of this.filasPestana()) {
      const v = this.valorFiltrable(f, campo);
      if (!orden.has(v)) orden.set(v, campo === 'importe' ? f.importe : campo === 'fecha' ? (f.fecha ?? '') : v.toLowerCase());
    }
    const texto = normalizar(this.busquedaFiltro().trim());
    return [...orden.keys()]
      .filter((v) => !texto || normalizar(v).includes(texto))
      .sort((a, b) => {
        const x = orden.get(a)!;
        const y = orden.get(b)!;
        return typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'es');
      });
  });

  protected abrirFiltro(campo: CampoOrden, evento: MouseEvent): void {
    evento.stopPropagation();
    if (this.filtroAbierto()?.campo === campo) return this.filtroAbierto.set(null);
    this.anclaFiltro = evento.currentTarget as HTMLElement;
    this.busquedaFiltro.set('');
    this.filtroAbierto.set({ campo, top: 0, left: 0 });
    this.ubicarFiltro();
  }

  private anclaFiltro: HTMLElement | null = null;

  /** Pone el embudo debajo del botón de su columna (también al moverse la página). */
  protected ubicarFiltro(): void {
    const abierto = this.filtroAbierto();
    if (!abierto || !this.anclaFiltro?.isConnected) return this.filtroAbierto.set(null);
    const r = this.anclaFiltro.getBoundingClientRect();
    this.filtroAbierto.set({ ...abierto, top: r.bottom + 6, left: Math.max(16, Math.min(r.left - 8, window.innerWidth - 288)) });
  }

  protected tieneFiltro(campo: CampoOrden): boolean {
    return !!this.filtros()[campo]?.size;
  }

  /** Sin filtro todo se ve, así que todo está marcado. */
  protected marcado(campo: CampoOrden, valor: string): boolean {
    const valores = this.filtros()[campo];
    return !valores?.size || valores.has(valor);
  }

  protected alternarValor(campo: CampoOrden, valor: string): void {
    this.filtros.update((filtros) => {
      // Sin filtro todo está marcado: desmarcar uno es quedarse con los demás.
      const actual = filtros[campo]?.size ? new Set(filtros[campo]) : new Set(this.filasPestana().map((f) => this.valorFiltrable(f, campo)));
      if (!actual.delete(valor)) actual.add(valor);
      return { ...filtros, [campo]: actual };
    });
  }

  /** "Seleccionar todo" de lo que se ve en la lista (con lo tipeado en el embudo, solo eso). */
  protected alternarTodos(campo: CampoOrden): void {
    const visibles = this.valoresUnicos();
    const todos = visibles.every((v) => this.marcado(campo, v));
    this.filtros.update((filtros) => ({
      ...filtros,
      [campo]: todos ? new Set(['\u0000']) : this.busquedaFiltro().trim() ? new Set(visibles) : new Set<string>(),
    }));
  }

  protected readonly todosMarcados = computed(() => {
    const abierto = this.filtroAbierto();
    const visibles = this.valoresUnicos();
    return !!abierto && visibles.length > 0 && visibles.every((v) => this.marcado(abierto.campo, v));
  });

  protected limpiarFiltro(campo: CampoOrden): void {
    this.filtros.update(({ [campo]: _, ...resto }) => resto);
  }

  protected ordenarComo(campo: CampoOrden, dir: 1 | -1): void {
    this.orden.set({ campo, dir });
  }

  /** Parte el texto para pintar en amarillo lo que coincide con la búsqueda (sin importar acentos). */
  protected partes(texto: string | null | undefined): { texto: string; marca: boolean }[] {
    const crudo = texto ?? '';
    const termino = normalizar(this.busqueda().trim());
    if (!termino || !crudo) return [{ texto: crudo, marca: false }];
    const origen = normalizar(crudo);
    const partes: { texto: string; marca: boolean }[] = [];
    let desde = 0;
    for (let i = origen.indexOf(termino); i !== -1; i = origen.indexOf(termino, desde)) {
      if (i > desde) partes.push({ texto: crudo.slice(desde, i), marca: false });
      partes.push({ texto: crudo.slice(i, i + termino.length), marca: true });
      desde = i + termino.length;
    }
    if (desde < crudo.length) partes.push({ texto: crudo.slice(desde), marca: false });
    return partes;
  }

  // --- Filas desplegables: lo principal a la vista y el resto en un cuadro que se abre
  private readonly desplegadas = linkedSignal<Pestana | null, Set<string>>({
    source: () => this.pestanaActiva(),
    computation: () => new Set(),
  });
  protected readonly todosDesplegados = computed(() => {
    const filas = this.filas().filter((f) => !!f.id);
    return filas.length > 0 && filas.every((f) => this.desplegadas().has(f.id!));
  });

  protected desplegada(item: Retencion): boolean {
    return !!item.id && this.desplegadas().has(item.id);
  }

  protected alternarDesplegada(item: Retencion): void {
    if (!item.id || this.borrador()) return;
    this.desplegadas.update((s) => {
      const nueva = new Set(s);
      if (!nueva.delete(item.id!)) nueva.add(item.id!);
      return nueva;
    });
  }

  protected alternarTodosDesplegados(): void {
    this.desplegadas.set(
      this.todosDesplegados() ? new Set() : new Set(this.filas().flatMap((f) => (f.id ? [f.id] : []))),
    );
  }

  /** La tabla va en una caja de alto mediano; "Ver todo" la muestra entera. */
  protected readonly tablaExpandida = signal(false);

  /** «Enviar a revisión»: el formulario abierto, el envío en curso y lo último enviado. */
  private readonly revisiones = inject(RevisionesStore);
  protected readonly envio = signal<EnvioRevision | null>(null);
  protected readonly enviando = signal(false);
  protected readonly errorEnvio = signal<string | null>(null);
  protected readonly enviada = signal<Revision | null>(null);

  protected abrirEnvio(): void {
    const hasta = this.store.resumen()?.hasta_periodo;
    this.errorEnvio.set(null);
    this.envio.set({
      empresa: '',
      periodo: hasta ? `${hasta.slice(5, 7)}/${hasta.slice(0, 4)}` : '',
      enviado_por: this.revisiones.autor(),
      comentario: '',
    });
  }

  protected campoEnvio(campo: keyof EnvioRevision, valor: string): void {
    this.envio.update((f) => (f ? { ...f, [campo]: valor } : f));
  }

  protected enviar(): void {
    const datos = this.envio();
    const taskId = this.store.taskId();
    if (!datos || !taskId || this.enviando()) return;
    this.enviando.set(true);
    this.errorEnvio.set(null);
    this.revisiones.enviar(taskId, datos).subscribe({
      next: (revision) => {
        this.enviando.set(false);
        this.envio.set(null);
        this.enviada.set(revision);
      },
      error: () => {
        this.enviando.set(false);
        this.errorEnvio.set('No se pudo enviar. Si la conciliación venció, volvé a traer los datos.');
      },
    });
  }

  protected readonly fechaCorta = fechaCorta;
  protected readonly pesos = (n: number) => PESOS.format(n);

  protected readonly totalFiltrado = computed(() => this.filas().reduce((s, f) => s + (this.eliminado(f) ? 0 : f.importe), 0));
  protected readonly cantidadFiltros = computed(() => Object.values(this.filtros()).filter((v) => v.size).length);
  protected readonly hayFiltro = computed(() => !!this.busqueda().trim() || this.cantidadFiltros() > 0);

  protected limpiarFiltros(): void {
    this.busqueda.set('');
    this.filtros.set({});
  }

  /** Desde la tabla por impuesto: deja en el detalle solo ese impuesto. */
  protected filtrarImpuesto(impuesto: string): void {
    this.filtros.set({ impuesto: new Set([impuesto || 'Sin identificar']) });
  }

  // --- Avisar al cliente lo que falta en ARCA
  /** Ids de "Faltan en ARCA" elegidos; se vacía con cada resultado nuevo o al emparejar. */
  protected readonly seleccion = linkedSignal<unknown, Set<string>>({
    source: () => this.store.detalle(),
    computation: () => new Set(),
  });
  protected readonly seleccionadas = computed(() => {
    const ids = this.seleccion();
    return (this.store.detalle()?.faltan_en_arca ?? []).filter((m) => !!m.id && ids.has(m.id)).map(this.conEdicion);
  });
  protected readonly totalSeleccionado = computed(() => this.seleccionadas().reduce((s, m) => s + m.importe, 0));
  /** Cuántos de "Faltan en ARCA" hay elegidos: ninguno, algunos o todos. */
  protected readonly marcaTodas = computed<'todas' | 'algunas' | 'ninguna'>(() => {
    const elegidas = this.seleccionadas().length;
    const total = this.store.detalle()?.faltan_en_arca.length ?? 0;
    return !elegidas ? 'ninguna' : elegidas === total ? 'todas' : 'algunas';
  });

  protected elegida(item: Retencion): boolean {
    return !!item.id && this.seleccion().has(item.id);
  }

  protected alternar(item: Retencion): void {
    if (!item.id) return;
    this.seleccion.update((s) => {
      const nueva = new Set(s);
      if (!nueva.delete(item.id!)) nueva.add(item.id!);
      return nueva;
    });
  }

  protected quitarSeleccion(): void {
    this.seleccion.set(new Set());
  }

  /** Elige todos los de "Faltan en ARCA" (también los que no entran en la tarjeta) o ninguno. */
  protected alternarTodas(): void {
    const todas = this.store.detalle()?.faltan_en_arca ?? [];
    this.seleccion.set(
      this.marcaTodas() === 'todas' ? new Set() : new Set(todas.flatMap((m) => (m.id ? [m.id] : []))),
    );
  }

  protected readonly aviso = signal<string | null>(null);
  protected readonly copiado = signal(false);

  protected abrirAviso(): void {
    this.copiado.set(false);
    this.aviso.set(armarAviso(this.seleccionadas()));
  }

  protected async copiarAviso(): Promise<void> {
    const texto = this.aviso();
    if (!texto) return;
    try {
      await navigator.clipboard.writeText(texto);
      this.copiado.set(true);
      setTimeout(() => this.copiado.set(false), 2000);
    } catch {
      this.copiado.set(false);
    }
  }

  protected enviarPorCorreo(): void {
    const texto = this.aviso();
    if (!texto) return;
    const asunto = 'Retenciones y percepciones que no figuran en ARCA';
    window.location.href = `mailto:?subject=${encodeURIComponent(asunto)}&body=${encodeURIComponent(texto)}`;
  }

  /** Porcentaje de lo informado por ARCA que quedó conciliado (por importe). */
  protected readonly avance = computed(() => {
    const r = this.store.resumen();
    if (!r?.importe_arca) return 0;
    return r.importe_conciliado / r.importe_arca;
  });

  // --- Emparejar a mano
  protected readonly eligiendoPara = signal<Retencion | null>(null);
  protected readonly busquedaPar = signal('');
  /** Los asientos sin certificado, del más parecido al menos parecido. */
  protected readonly candidatos = computed(() => {
    const r = this.eligiendoPara();
    const d = this.store.detalle();
    if (!r || !d) return [];
    const texto = normalizar(this.busquedaPar().trim());
    const fechaR = r.fecha ? Date.parse(r.fecha) : null;
    return d.faltan_en_arca
      .filter(
        (m) =>
          !texto ||
          [m.razon_social, m.documento, m.descripcion, m.cuenta, String(m.importe)].some((v) => normalizar(v).includes(texto)),
      )
      .map((m) => ({
        mov: m,
        diferencia: r.importe - m.importe,
        dias: fechaR !== null && m.fecha ? Math.round(Math.abs(Date.parse(m.fecha) - fechaR) / 86_400_000) : null,
        mismoImpuesto: !r.impuesto || !m.impuesto || r.impuesto === m.impuesto,
      }))
      .sort(
        (a, b) =>
          Math.abs(a.diferencia) - Math.abs(b.diferencia) ||
          Number(b.mismoImpuesto) - Number(a.mismoImpuesto) ||
          (a.dias ?? 9999) - (b.dias ?? 9999),
      );
  });

  protected elegirPestana(id: Pestana): void {
    this.pestanaActiva.set(id);
    this.store.errorPar.set(null);
  }

  protected ordenarPor(campo: CampoOrden): void {
    this.orden.update((o) => ({ campo, dir: o.campo === campo ? (-o.dir as 1 | -1) : campo === 'importe' ? -1 : 1 }));
  }

  protected flecha(campo: CampoOrden): string {
    const o = this.orden();
    return o.campo !== campo ? '' : o.dir === 1 ? '↑' : '↓';
  }

  protected cantidad(id: Pestana): number {
    if (id === 'revisar') return this.sugerencias().length;
    return this.store.detalle()?.[id].length ?? 0;
  }

  protected importe(id: Pestana): number {
    if (id === 'revisar') return this.sugerencias().reduce((s, x) => s + x.arca.importe, 0);
    return this.store.detalle()?.[id].reduce((s, f) => s + f.importe, 0) ?? 0;
  }

  protected criterio(item: Retencion): string {
    return item.criterio ? ETIQUETAS_CRITERIO[item.criterio] : '';
  }

  protected confirmar(s: Sugerencia): void {
    this.store.emparejar(s.id_arca, s.id_mayor);
  }

  protected descartar(s: Sugerencia): void {
    this.descartadas.update((d) => new Set(d).add(`${s.id_arca}|${s.id_mayor}`));
  }

  protected abrirEmparejar(item: Retencion): void {
    this.busquedaPar.set('');
    this.store.errorPar.set(null);
    this.eligiendoPara.set(item);
  }

  protected emparejarCon(mayor: Retencion): void {
    const arca = this.eligiendoPara();
    if (!arca?.id || !mayor.id) return;
    this.store.emparejar(arca.id, mayor.id);
    this.eligiendoPara.set(null);
  }

  protected importarOtros(): void {
    this.store.limpiar();
    this.router.navigate(['/importar']);
  }
}
