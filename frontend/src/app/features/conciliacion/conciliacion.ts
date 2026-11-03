import { CurrencyPipe, DatePipe, PercentPipe } from '@angular/common';
import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { Router } from '@angular/router';
import {
  CriterioConciliacion,
  DetalleConciliacion,
  Retencion,
  Sugerencia,
} from '../../core/models/conciliacion.models';
import { ConciliacionStore } from '../../core/services/conciliacion.store';

type Bandeja = keyof DetalleConciliacion;
type Pestana = Bandeja | 'revisar';
type CampoOrden = 'fecha' | 'razon_social' | 'importe' | 'impuesto';

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

function normalizar(texto: string | null | undefined): string {
  return (texto ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

@Component({
  selector: 'app-conciliacion',
  imports: [CurrencyPipe, DatePipe, PercentPipe],
  templateUrl: './conciliacion.html',
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
      filas: d[id].slice(0, CASOS_EN_RESUMEN),
      total: d[id].length,
    }));
  });

  /** Sugerencias que la persona descartó en esta pantalla (no se mandan al servidor). */
  private readonly descartadas = signal<Set<string>>(new Set());
  protected readonly sugerencias = computed(() =>
    this.store.sugerencias().filter((s) => !this.descartadas().has(`${s.id_arca}|${s.id_mayor}`)),
  );

  /** Arranca donde hay trabajo: lo dudoso, lo no declarado, lo sin certificado. */
  protected readonly pestanaActiva = linkedSignal<string | null, Pestana>({
    // Se elige al llegar un resultado nuevo, no al emparejar dentro del mismo.
    source: () => (this.store.resumen() ? this.store.taskId() : null),
    computation: () => {
      if (this.store.sugerencias().length) return 'revisar';
      const r = this.store.resumen();
      if (r?.total_faltan_en_mayor) return 'faltan_en_mayor';
      if (r?.total_faltan_en_arca) return 'faltan_en_arca';
      return 'conciliados';
    },
  });
  protected readonly config = computed(
    () => PESTANAS.find((p) => p.id === this.pestanaActiva()) ?? PESTANAS[0],
  );

  // --- Filtros y orden de la tabla
  protected readonly busqueda = signal('');
  protected readonly impuestoFiltro = signal<string>('');
  protected readonly orden = signal<{ campo: CampoOrden; dir: 1 | -1 }>({ campo: 'fecha', dir: 1 });

  protected readonly impuestos = computed(() => {
    const d = this.store.detalle();
    if (!d) return [];
    const todos = [...d.conciliados, ...d.faltan_en_mayor, ...d.faltan_en_arca].map((f) => f.impuesto || 'Sin identificar');
    return [...new Set(todos)].sort();
  });

  protected readonly filas = computed<Retencion[]>(() => {
    const pestana = this.pestanaActiva();
    const d = this.store.detalle();
    if (!d || pestana === 'revisar') return [];
    const texto = normalizar(this.busqueda().trim());
    const impuesto = this.impuestoFiltro();
    const { campo, dir } = this.orden();

    return d[pestana]
      .filter((f) => !impuesto || (f.impuesto || 'Sin identificar') === impuesto)
      .filter(
        (f) =>
          !texto ||
          [f.cuit, f.cuit.replace(/\D/g, ''), f.razon_social, f.certificado, f.documento, f.documento_mayor, f.descripcion, f.cuenta, String(f.importe)]
            .some((v) => normalizar(v).includes(texto)),
      )
      .slice()
      .sort((a, b) => {
        const va = a[campo] ?? '';
        const vb = b[campo] ?? '';
        return (typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb))) * dir;
      });
  });

  protected readonly totalFiltrado = computed(() => this.filas().reduce((s, f) => s + f.importe, 0));
  protected readonly hayFiltro = computed(() => !!this.busqueda().trim() || !!this.impuestoFiltro());

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
