import { Component, computed, effect, inject, linkedSignal, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { environment } from '../../../environments/environment';
import { PasoAutomatico } from '../../core/models/conciliacion.models';
import { ConsultaArca as Consulta, ImpuestoArca, TipoRetencion } from '../../core/models/conexiones.models';
import { ConciliacionStore } from '../../core/services/conciliacion.store';
import { CuitPipe } from '../../core/cuit.pipe';
import { ConexionesStore } from '../../core/services/conexiones.store';
import { NOMBRES_CORTOS, SUGERIDOS, SelectorImpuestos } from './selector-impuestos';

type Preset = 'mes-anterior' | 'mes-actual' | 'ultimos-3' | 'anio' | 'personalizado';
type EstadoPaso = 'pendiente' | 'actual' | 'hecho' | 'error';

function isoLocal(fecha: Date): string {
  const mes = String(fecha.getMonth() + 1).padStart(2, '0');
  const dia = String(fecha.getDate()).padStart(2, '0');
  return `${fecha.getFullYear()}-${mes}-${dia}`;
}

function desdeIso(iso: string): Date {
  const [anio, mes, dia] = iso.split('-').map(Number);
  return new Date(anio, mes - 1, dia);
}

/** [desde, hasta] de cada atajo de período, contado desde hoy. */
function rangoDe(preset: Exclude<Preset, 'personalizado'>, hoy = new Date()): [string, string] {
  const anio = hoy.getFullYear();
  const mes = hoy.getMonth();
  switch (preset) {
    case 'mes-anterior':
      return [isoLocal(new Date(anio, mes - 1, 1)), isoLocal(new Date(anio, mes, 0))];
    case 'mes-actual':
      return [isoLocal(new Date(anio, mes, 1)), isoLocal(hoy)];
    case 'ultimos-3':
      return [isoLocal(new Date(anio, mes - 3, 1)), isoLocal(new Date(anio, mes, 0))];
    case 'anio':
      return [isoLocal(new Date(anio, 0, 1)), isoLocal(hoy)];
  }
}

const ETIQUETAS_TIPO: Record<TipoRetencion, string> = {
  RetencionPercepcion: 'Retención y/o Percepción',
  Retencion: 'Retención',
  Percepcion: 'Percepción',
};

interface ImpuestoConsultado {
  codigo: string;
  nombre: string;
  /** Los que ARCA admite con este impuesto. */
  tipos: { id: TipoRetencion; etiqueta: string }[];
  /** El que ARCA marca solo al elegir el impuesto. */
  predeterminado: TipoRetencion;
}

/** Orden en que ARCA muestra los tipos. */
const ORDEN_TIPOS: TipoRetencion[] = ['RetencionPercepcion', 'Retencion', 'Percepcion'];

/** El tipo que ARCA marca con el impuesto: el combinado si lo admite, si no el único que muestra. */
function tipoDeArca(impuesto: ImpuestoArca): TipoRetencion {
  const tipos = impuesto.tipos ?? [];
  return !tipos.length || tipos.includes('RetencionPercepcion') ? 'RetencionPercepcion' : tipos[0];
}

function nombreLegible({ codigo, descripcion }: ImpuestoArca): string {
  const original = descripcion.replace(new RegExp(`^${codigo}\\s*-\\s*`), '').trim();
  return original.charAt(0) + original.slice(1).toLowerCase();
}

function mismasListas(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((valor, i) => valor === b[i]);
}

const FORMATO_DIA =new Intl.DateTimeFormat('es-AR', { day: 'numeric', month: 'long', year: 'numeric' });

const PASOS: { id: PasoAutomatico; titulo: string; ayuda: string }[] = [
  { id: 'arca', titulo: 'Retenciones en ARCA', ayuda: 'Mis Retenciones del mes, el impuesto y el tipo' },
  { id: 'xubio', titulo: 'Libro Mayor de Xubio', ayuda: 'Mismas fechas, la cuenta de ese impuesto y tipo' },
  { id: 'cruce', titulo: 'Conciliación', ayuda: 'Cruce por certificado, CUIT, importe y fecha' },
];

/**
 * La consulta automática: con los dos bots conectados, el de Xubio baja el
 * Libro Mayor del período, el de ARCA consulta Mis Retenciones (con los
 * impuestos elegidos o los que detecta en el mayor) y se cruzan. Las opciones
 * del formulario (CUITs del retenido e impuestos) las lee el bot de ARCA.
 */
@Component({
  selector: 'app-consulta-arca',
  imports: [CuitPipe, SelectorImpuestos, RouterLink],
  templateUrl: './consulta-arca.html',
})
export class ConsultaArca {
  protected readonly conexiones = inject(ConexionesStore);
  protected readonly store = inject(ConciliacionStore);
  private readonly router = inject(Router);

  protected readonly formulario = computed(() => this.conexiones.arca()?.formulario ?? null);
  protected readonly arcaConectado = computed(() => this.conexiones.arca()?.estado === 'conectado');
  protected readonly xubioConectado = computed(() => this.conexiones.xubio()?.estado === 'conectado');
  protected readonly consultandoArca = computed(() => !!this.conexiones.arca()?.consultando);

  // --- Período: las mismas fechas en ARCA y en Xubio
  protected readonly presets: { id: Exclude<Preset, 'personalizado'>; etiqueta: string }[] = [
    { id: 'mes-anterior', etiqueta: 'Mes anterior' },
    { id: 'mes-actual', etiqueta: 'Este mes' },
    { id: 'ultimos-3', etiqueta: 'Últimos 3 meses' },
    { id: 'anio', etiqueta: 'Año en curso' },
  ];
  protected readonly preset = signal<Preset>('mes-anterior');
  protected readonly desde = signal(rangoDe('mes-anterior')[0]);
  protected readonly hasta = signal(rangoDe('mes-anterior')[1]);
  protected readonly resumenPeriodo = computed(() => {
    if (!this.desde() || !this.hasta()) return '';
    return `Del ${FORMATO_DIA.format(desdeIso(this.desde()))} al ${FORMATO_DIA.format(desdeIso(this.hasta()))}`;
  });

  // El estado de los bots llega cada 2 s como un objeto nuevo: lo que eligió la
  // persona se ata a las listas por su contenido, no al objeto, para que el
  // sondeo no lo pise.
  private readonly cuitsOfrecidos = computed(() => this.formulario()?.cuits ?? [], { equal: mismasListas });
  private readonly impuestosOfrecidos = computed(
    () => (this.formulario()?.impuestos ?? []).map((i) => i.codigo),
    { equal: mismasListas },
  );

  // --- Impuestos
  /**
   * El impuesto elegido (uno solo, como en ARCA; en lista porque la consulta
   * los manda así). Arranca con Ganancias, o IVA, si ARCA los ofrece.
   */
  protected readonly impuestos = linkedSignal<string[], string[]>({
    source: this.impuestosOfrecidos,
    computation: (ofrecidos, anterior) =>
      anterior
        ? anterior.value.filter((c) => ofrecidos.includes(c))
        : SUGERIDOS.filter((c) => ofrecidos.includes(c)).slice(0, 1),
  });

  // --- Más opciones
  protected readonly avanzadas = signal(false);
  /**
   * La que se eligió, mientras ARCA la siga ofreciendo. Si no, la
   * predeterminada, la del login o la primera.
   */
  protected readonly cuitRetenido = linkedSignal<string[], string>({
    source: this.cuitsOfrecidos,
    computation: (cuits, anterior) => {
      if (anterior && cuits.includes(anterior.value)) return anterior.value;
      const login = this.conexiones.arca()?.cuit ?? '';
      return [environment.cuitRetenidoPredeterminado, login].find((c) => cuits.includes(c)) ?? cuits[0] ?? '';
    },
  });
  protected readonly cuitAgente = signal('');

  /** La empresa con la que se entró a Xubio en Conexiones: de ella sale el Libro Mayor. */
  protected readonly empresaXubio = computed(() => this.conexiones.xubio()?.empresa_activa ?? null);

  /**
   * El impuesto elegido con los tipos que ARCA admite con él (como en el
   * formulario, que oculta los que no corresponden).
   */
  protected readonly consultado = computed<ImpuestoConsultado | null>(() => {
    const impuesto = (this.formulario()?.impuestos ?? []).find((i) => i.codigo === this.impuestos()[0]);
    if (!impuesto) return null;
    const admitidos = impuesto.tipos?.length ? impuesto.tipos : ORDEN_TIPOS;
    return {
      codigo: impuesto.codigo,
      nombre: NOMBRES_CORTOS[impuesto.codigo] ?? nombreLegible(impuesto),
      tipos: ORDEN_TIPOS.filter((t) => admitidos.includes(t)).map((id) => ({ id, etiqueta: ETIQUETAS_TIPO[id] })),
      predeterminado: tipoDeArca(impuesto),
    };
    // El sondeo trae el formulario cada 2 s: es el mismo impuesto mientras no cambie el código ni sus tipos.
  }, { equal: (a, b) => a?.codigo === b?.codigo && a?.tipos.map((t) => t.id).join() === b?.tipos.map((t) => t.id).join() });

  /**
   * Retención, percepción o las dos, como el filtro de ARCA: al elegir un
   * impuesto vuelve al que ARCA marca solo ("Retención y/o Percepción" si lo
   * admite), aunque antes se haya elegido otro.
   */
  protected readonly tipo = linkedSignal<ImpuestoConsultado | null, TipoRetencion>({
    source: this.consultado,
    computation: (actual) => actual?.predeterminado ?? 'RetencionPercepcion',
  });

  /**
   * Qué cuenta va a buscar el bot en «Cuenta» del Libro Mayor de Xubio: la
   * del impuesto y el tipo elegidos. El nombre exacto sale del plan de cuentas
   * de la empresa, no está fijo.
   */
  protected readonly cuentaABuscar = computed(() => {
    const codigo = this.consultado()?.codigo;
    const impuesto = (this.formulario()?.impuestos ?? []).find((i) => i.codigo === codigo)?.impuesto ?? 'ese impuesto';
    const tipo = { Retencion: 'Retención', Percepcion: 'Percepción', RetencionPercepcion: 'Retención y la de Percepción' }[this.tipo()];
    return `${tipo} de ${impuesto} sufridas`;
  });

  protected readonly falta = signal<string | null>(null);

  // --- Avance del flujo automático
  protected readonly pasos = PASOS;
  protected readonly enCurso = computed(
    () => this.store.origen() === 'automatico' && !['inicial', 'cancelado'].includes(this.store.fase()),
  );
  /** Se tocó «Cancelar» en «Traer y conciliar»: el formulario vuelve para corregir y reintentar. */
  protected readonly cancelado = computed(() => this.store.origen() === 'automatico' && this.store.fase() === 'cancelado');
  /** La sesión de ARCA venció y el bot está entrando de nuevo con los mismos datos. */
  protected readonly renovandoArca = computed(() => !!this.conexiones.arca()?.renovando);
  protected readonly ocupado = computed(() => this.store.ocupado() || this.consultandoArca() || this.conexiones.enviando());
  protected readonly puedeTraerTodo = computed(
    () => this.arcaConectado() && !!this.formulario() && this.xubioConectado() && !this.ocupado(),
  );

  /** Solo se va a los resultados si el cruce se pidió desde acá. */
  private readonly esperandoResultado = signal(false);

  constructor() {
    effect(() => {
      if (this.esperandoResultado() && this.store.fase() === 'completado') {
        this.router.navigate(['/conciliacion']);
      }
    });
  }

  protected elegirPreset(preset: Exclude<Preset, 'personalizado'>): void {
    const [desde, hasta] = rangoDe(preset);
    this.preset.set(preset);
    this.desde.set(desde);
    this.hasta.set(hasta);
    this.falta.set(null);
  }

  protected cambiarFecha(cual: 'desde' | 'hasta', valor: string): void {
    (cual === 'desde' ? this.desde : this.hasta).set(valor);
    this.preset.set('personalizado');
    this.falta.set(null);
  }

  protected estadoPaso(paso: PasoAutomatico): EstadoPaso {
    const fase = this.store.fase();
    if (fase === 'completado') return 'hecho';
    const actual = PASOS.findIndex((p) => p.id === (this.store.paso() ?? 'arca'));
    const indice = PASOS.findIndex((p) => p.id === paso);
    if (indice < actual) return 'hecho';
    if (indice > actual) return 'pendiente';
    return fase === 'error' ? 'error' : 'actual';
  }

  protected traerYConciliar(): void {
    const consulta = this.armarConsulta();
    if (!consulta) return;
    this.esperandoResultado.set(true);
    this.store.conciliarAutomatico(consulta);
  }

  protected soloArca(): void {
    const consulta = this.armarConsulta();
    if (consulta) this.conexiones.consultarArca(consulta);
  }

  private armarConsulta(): Consulta | null {
    const desde = this.desde();
    const hasta = this.hasta();
    const cuitAgente = this.cuitAgente().replace(/\D/g, '');
    const impuestos = this.impuestos();

    let falta: string | null = null;
    if (!desde || !hasta) falta = 'Completá las fechas del período.';
    else if (desde > hasta) falta = 'La fecha desde es posterior a la fecha hasta.';
    else if (!impuestos.length) falta = 'Elegí el impuesto retenido.';
    else if (cuitAgente && cuitAgente.length !== 11) falta = 'La CUIT del agente tiene que tener 11 dígitos.';
    else if (!this.cuitRetenido()) falta = 'ARCA no ofreció ninguna CUIT del retenido.';    this.falta.set(falta);
    if (falta) return null;

    return {
      cuit_retenido: this.cuitRetenido(),
      cuit_agente: cuitAgente || null,
      impuestos,
      tipo: this.tipo(),
      desde,
      hasta,
    };
  }
}
