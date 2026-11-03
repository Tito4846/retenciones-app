import { Component, computed, effect, inject, linkedSignal, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { environment } from '../../../environments/environment';
import { PasoAutomatico } from '../../core/models/conciliacion.models';
import { ConsultaArca as Consulta, ImpuestoArca, TipoRetencion } from '../../core/models/conexiones.models';
import { ConciliacionStore } from '../../core/services/conciliacion.store';
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

function mismasListas(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((valor, i) => valor === b[i]);
}

const FORMATO_DIA =new Intl.DateTimeFormat('es-AR', { day: 'numeric', month: 'long', year: 'numeric' });

const PASOS: { id: PasoAutomatico; titulo: string; ayuda: string }[] = [
  { id: 'xubio', titulo: 'Libro Mayor de Xubio', ayuda: 'Las cuentas de retenciones del período' },
  { id: 'arca', titulo: 'Retenciones en ARCA', ayuda: 'Mis Retenciones, un impuesto por vez' },
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
  imports: [SelectorImpuestos, RouterLink],
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

  // --- Período
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
  protected readonly modoImpuestos = signal<'auto' | 'manual'>('manual');
  /** Arranca con Ganancias e IVA, si ARCA los ofrece; después, lo que se elija. */
  protected readonly impuestos = linkedSignal<string[], string[]>({
    source: this.impuestosOfrecidos,
    computation: (ofrecidos, anterior) =>
      anterior
        ? anterior.value.filter((c) => ofrecidos.includes(c))
        : SUGERIDOS.filter((c) => ofrecidos.includes(c)),
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

  // --- Tipo: lo que ARCA admite con los impuestos elegidos
  protected readonly tipos: { valor: TipoRetencion; etiqueta: string }[] = [
    { valor: 'RetencionPercepcion', etiqueta: 'Retención y/o Percepción' },
    { valor: 'Retencion', etiqueta: 'Retención' },
    { valor: 'Percepcion', etiqueta: 'Percepción' },
  ];

  /** Los impuestos elegidos, con los tipos que ARCA admite para cada uno. */
  private readonly impuestosElegidos = computed(() => {
    const porCodigo = new Map((this.formulario()?.impuestos ?? []).map((i) => [i.codigo, i]));
    return this.impuestos()
      .map((codigo) => porCodigo.get(codigo))
      .filter((i): i is ImpuestoArca => !!i);
  });

  /**
   * Los tipos que se pueden elegir: los que admita al menos uno de los
   * impuestos elegidos (como en ARCA, que oculta los que no corresponden).
   * Detectando solos los impuestos no se sabe cuáles van: se ofrecen todos.
   */
  protected readonly tiposPermitidos = computed<TipoRetencion[]>(
    () => {
      const todos = this.tipos.map((t) => t.valor);
      const elegidos = this.impuestosElegidos();
      if (this.modoImpuestos() === 'auto' || !elegidos.length) return todos;
      if (elegidos.some((i) => !i.tipos?.length)) return todos;
      return todos.filter((t) => elegidos.some((i) => i.tipos!.includes(t)));
    },
    { equal: mismasListas },
  );

  /**
   * El tipo se acomoda solo: si el elegido deja de valer para los impuestos,
   * pasa al combinado (o al único que ARCA admita).
   */
  protected readonly tipo = linkedSignal<TipoRetencion[], TipoRetencion>({
    source: this.tiposPermitidos,
    computation: (permitidos, anterior) =>
      anterior && permitidos.includes(anterior.value)
        ? anterior.value
        : permitidos.includes('RetencionPercepcion')
          ? 'RetencionPercepcion'
          : permitidos[0],
  });

  /** Impuestos elegidos que no admiten el tipo elegido: ARCA los consulta combinados. */
  protected readonly avisoTipo = computed(() => {
    if (this.modoImpuestos() === 'auto') return null;
    const tipo = this.tipo();
    const nombres = this.impuestosElegidos()
      .filter((i) => i.tipos?.length && !i.tipos.includes(tipo))
      .map((i) => `${NOMBRES_CORTOS[i.codigo] ?? i.codigo} (${i.codigo})`);
    if (!nombres.length) return null;
    return `${nombres.join(', ')}: ARCA solo admite «Retención y/o Percepción», así que ${
      nombres.length > 1 ? 'esos se consultan' : 'ese se consulta'
    } combinado.`;
  });

  protected readonly falta = signal<string | null>(null);

  // --- Avance del flujo automático
  protected readonly pasos = PASOS;
  protected readonly enCurso = computed(() => this.store.origen() === 'automatico' && this.store.fase() !== 'inicial');
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
    const actual = PASOS.findIndex((p) => p.id === (this.store.paso() ?? 'xubio'));
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
    const impuestos = this.modoImpuestos() === 'auto' ? [] : this.impuestos();

    let falta: string | null = null;
    if (!desde || !hasta) falta = 'Completá las fechas del período.';
    else if (desde > hasta) falta = 'La fecha desde es posterior a la fecha hasta.';
    else if (this.modoImpuestos() === 'manual' && !impuestos.length) falta = 'Elegí al menos un impuesto.';
    else if (cuitAgente && cuitAgente.length !== 11) falta = 'La CUIT del agente tiene que tener 11 dígitos.';
    else if (!this.cuitRetenido()) falta = 'ARCA no ofreció ninguna CUIT del retenido.';
    this.falta.set(falta);
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
