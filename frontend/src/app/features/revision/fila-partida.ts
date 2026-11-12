import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, linkedSignal, signal } from '@angular/core';
import { CuitPipe } from '../../core/cuit.pipe';
import { Adjunto, CambioPartida, Partida } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { diferenciaFila, ESTADO_FILA, EstadoFila, leerImporte } from './revision.reglas';
import { peso, pesos } from './revision.util';

const FALTANTE = 'bg-red-50 text-red-700 ring-red-200';
const OBSERVADA = 'bg-amber-50 text-amber-800 ring-amber-200';

const MOTIVOS: Record<string, { texto: string; clase: string }> = {
  D01: { texto: 'Diferencia de monto', clase: OBSERVADA },
  P01: { texto: 'Agente sin confirmar', clase: OBSERVADA },
  P02: { texto: 'CUIT distinta', clase: OBSERVADA },
  P03: { texto: 'Fuera de tolerancia', clase: OBSERVADA },
  P04: { texto: 'Emparejada a mano', clase: OBSERVADA },
  N01: { texto: 'Falta en el Mayor', clase: FALTANTE },
  N02: { texto: 'Falta en ARCA', clase: FALTANTE },
};

type Panel = 'ajustar' | 'descartar' | 'detalle' | null;

/**
 * Una excepción del lote: sus datos, el estado en un color, las acciones en la misma
 * fila y una zona para soltar el comprobante que la respalda. Es el `<tbody>` entero
 * (dos filas: los datos y el panel que se abre debajo).
 */
@Component({
  selector: 'tbody[appFilaPartida]',
  imports: [CuitPipe, DatePipe],
  host: {
    class: 'transition-colors [&_tr:first-child_td]:border-t [&_td]:border-line',
    '[class.bg-brand-50]': 'encima()',
    '[class.outline-2]': 'encima()',
    '[class.outline-dashed]': 'encima()',
    '[class.outline-brand-400]': 'encima()',
    '[class.-outline-offset-2]': 'encima()',
    '[attr.aria-busy]': 'subiendo() || null',
    '(dragenter)': 'sobre($event)',
    '(dragover)': 'sobre($event)',
    '(dragleave)': 'salir($event)',
    '(drop)': 'soltar($event)',
  },
  template: `
    @let p = partida();
    @let e = estados[estado()];
    <tr class="align-top" [class.opacity-60]="estado() === 'descartada'">
      <td class="px-2 pt-2 pb-1.5">
        <p class="font-semibold wrap-break-word">{{ p.razon_social || 'Sin razón social' }}</p>
        @if (p.cuit) { <p class="truncate text-muted tabular-nums">{{ p.cuit | cuit }}</p> }
        <span class="mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset" [class]="e.clase">
          <span class="h-1.5 w-1.5 rounded-full" [class]="e.punto" aria-hidden="true"></span>{{ e.texto }}
        </span>
      </td>
      <td class="px-2 pt-2 pb-1.5 wrap-anywhere">{{ p.certificado || p.documento_erp || '—' }}</td>
      <td class="px-2 pt-2 pb-1.5 text-right whitespace-nowrap tabular-nums">
        {{ pesos(p.importe_ajustado ?? p.importe_erp) }}
        @if (p.importe_ajustado !== null) {
          <p class="text-xs text-muted" title="Lo que decía el Mayor antes del ajuste">{{ p.importe_erp === null ? 'ajustado' : 'antes ' + pesos(p.importe_erp) }}</p>
        }
      </td>
      <td class="px-2 pt-2 pb-1.5 text-right whitespace-nowrap tabular-nums">{{ pesos(p.importe_fisco) }}</td>
      <td class="px-2 pt-2 pb-1.5 text-right font-semibold whitespace-nowrap tabular-nums"
        [class.text-red-700]="diferencia() !== 0" [class.text-emerald-700]="diferencia() === 0">{{ pesos(diferencia() / 100) }}</td>
      <td class="px-2 pt-2 pb-1.5">
        <div class="flex flex-wrap gap-1">
          @for (m of motivos(); track m.texto) {
            <span class="rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset" [class]="m.clase" [title]="m.ayuda">{{ m.texto }}</span>
          }
        </div>
      </td>
      <td class="px-1 pt-1.5 pb-1.5">
        <div class="flex justify-end gap-0.5">
          @if (editar()) {
            <button type="button" (click)="abrir('ajustar')" [disabled]="store.ocupado() || estado() === 'descartada'" title="Ajustar" aria-label="Ajustar"
              [attr.aria-expanded]="panel() === 'ajustar'" [class]="boton('ajustar')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
            </button>
            @if (estado() === 'descartada') {
              <button type="button" (click)="restaurar()" [disabled]="store.ocupado()" title="Volver a incluir en el cruce" aria-label="Volver a incluir" [class]="boton(null)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5" /></svg>
              </button>
            } @else {
              <button type="button" (click)="abrir('descartar')" [disabled]="store.ocupado()" title="Descartar del cruce" aria-label="Descartar"
                [attr.aria-expanded]="panel() === 'descartar'" [class]="boton('descartar')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            }
          }
          <button type="button" (click)="abrir('detalle')" title="Ver detalle" aria-label="Ver detalle" [attr.aria-expanded]="panel() === 'detalle'" [class]="boton('detalle')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>
            @if (adjuntos().length) {
              <span class="absolute -top-1 -right-1 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-brand-700 px-0.5 text-[9px] font-bold text-white tabular-nums">{{ adjuntos().length }}</span>
            }
          </button>
        </div>
      </td>
    </tr>

    <tr>
      <td colspan="7" class="px-2 pb-2 text-xs">
        @if (encima()) {
          <p class="rounded-md bg-white/70 py-1.5 text-center font-semibold text-brand-700">Soltá para adjuntarlo a {{ p.razon_social || 'esta partida' }}</p>
        } @else if (subiendo()) {
          <p class="text-brand-700">Adjuntando…</p>
        } @else if (p.justificacion || p.comentario || p.editado_por) {
          <p>
            @if (justificacion()) { <span class="font-semibold">{{ justificacion() }}</span> }
            @if (p.comentario) { <span>{{ justificacion() ? ' · ' : '' }}{{ p.comentario }}</span> }
            @if (p.editado_por) { <span class="text-muted"> — {{ p.editado_por }}</span> }
          </p>
        } @else if (editar()) {
          <p class="text-muted">Sin justificar · soltá acá el comprobante de esta partida</p>
        }
        @if (rechazo(); as r) { <p class="mt-1 font-semibold text-red-700" role="alert">{{ r }}</p> }

        @switch (panel()) {
          @case ('ajustar') {
            <form (submit)="$event.preventDefault(); guardarAjuste()" class="mt-2 grid gap-2 rounded-lg border border-line bg-canvas p-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
              <label class="block">
                <span class="font-semibold">Justificación</span>
                <select [value]="borrador().justificacion" (change)="campo('justificacion', $any($event.target).value)"
                  class="mt-1 w-full rounded-md border border-line bg-white px-2 py-1.5 text-sm outline-none focus:border-brand-400">
                  <option value="">Elegí una…</option>
                  @for (j of justificaciones(); track j[0]) { <option [value]="j[0]">{{ j[1] }}</option> }
                </select>
              </label>
              <label class="block">
                <span class="font-semibold">Importe ajustado</span>
                <input type="text" inputmode="decimal" [value]="borrador().importe" (input)="campo('importe', $any($event.target).value)"
                  [placeholder]="p.importe_fisco ?? '0,00'" aria-describedby="ayuda-importe"
                  class="mt-1 w-full rounded-md border bg-white px-2 py-1.5 text-right text-sm tabular-nums outline-none focus:border-brand-400"
                  [class]="importeInvalido() ? 'border-red-400' : 'border-line'" />
              </label>
              <label class="block sm:col-span-2">
                <span class="font-semibold">Comentario</span>
                <textarea rows="2" [value]="borrador().comentario" (input)="campo('comentario', $any($event.target).value)"
                  placeholder="Ej.: NC 0003-00001234 del agente, registrada en diciembre"
                  class="mt-1 w-full rounded-md border border-line bg-white px-2 py-1.5 text-sm outline-none focus:border-brand-400"></textarea>
              </label>
              <p id="ayuda-importe" class="text-muted sm:col-span-2">
                Importe ajustado: lo que debería decir el Mayor. Vacío = sin ajuste. Para que la fila quede en cero usá {{ pesos(p.importe_fisco) }}.
              </p>
              <div class="flex justify-end gap-2 sm:col-span-2">
                <button type="button" (click)="panel.set(null)" class="rounded-md px-3 py-1.5 font-semibold text-muted hover:bg-white">Cancelar</button>
                <button type="submit" [disabled]="store.ocupado() || importeInvalido()"
                  class="rounded-md bg-brand-700 px-3 py-1.5 font-semibold text-white hover:bg-brand-800 disabled:opacity-50">Guardar</button>
              </div>
            </form>
          }
          @case ('descartar') {
            <form (submit)="$event.preventDefault(); descartar()" class="mt-2 rounded-lg border border-red-200 bg-red-50 p-3">
              <label class="block">
                <span class="font-semibold text-red-900">Por qué se descarta del cruce</span>
                <textarea rows="2" [value]="motivo()" (input)="motivo.set($any($event.target).value)"
                  placeholder="Ej.: Retención duplicada en ARCA, el agente la anuló"
                  class="mt-1 w-full rounded-md border border-red-200 bg-white px-2 py-1.5 text-sm outline-none focus:border-red-400"></textarea>
              </label>
              <p class="mt-1 text-red-900/80">Deja de sumar en los totales, pero queda registrada y se puede volver a incluir.</p>
              <div class="mt-2 flex justify-end gap-2">
                <button type="button" (click)="panel.set(null)" class="rounded-md px-3 py-1.5 font-semibold text-muted hover:bg-white">Cancelar</button>
                <button type="submit" [disabled]="store.ocupado() || !motivo().trim()"
                  class="rounded-md bg-red-600 px-3 py-1.5 font-semibold text-white hover:bg-red-700 disabled:opacity-50">Descartar</button>
              </div>
            </form>
          }
          @case ('detalle') {
            <div class="mt-2 grid gap-x-6 gap-y-1 rounded-lg border border-line bg-canvas p-3 sm:grid-cols-2">
              <p><span class="text-muted">Impuesto:</span> {{ p.impuesto || '—' }}</p>
              <p><span class="text-muted">Criterio del cruce:</span> {{ p.criterio || '—' }}</p>
              <p><span class="text-muted">Fecha ARCA:</span> {{ p.fecha_fisco ? (p.fecha_fisco | date: 'dd/MM/yyyy') : '—' }}</p>
              <p><span class="text-muted">Fecha Mayor:</span> {{ p.fecha_erp ? (p.fecha_erp | date: 'dd/MM/yyyy') : '—' }}</p>
              <p><span class="text-muted">Certificado:</span> {{ p.certificado || '—' }}</p>
              <p><span class="text-muted">Asiento:</span> {{ p.documento_erp || '—' }}</p>
              @for (f of p.faltas; track f) { <p class="text-amber-800 sm:col-span-2">• {{ f }}</p> }
              <div class="sm:col-span-2">
                <p class="mt-1 font-semibold">Comprobantes de esta partida</p>
                @if (adjuntos().length) {
                  <ul class="mt-1 flex flex-wrap gap-1.5">
                    @for (a of adjuntos(); track a.id) {
                      <li class="flex max-w-full items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1">
                        <button type="button" (click)="store.bajarAdjunto(loteId(), a.id, a.nombre)" class="min-w-0 truncate font-semibold text-brand-700 hover:underline">{{ a.nombre }}</button>
                        <span class="shrink-0 text-muted tabular-nums">{{ peso(a.tamano) }}</span>
                        @if (editar()) {
                          <button type="button" (click)="store.retirarAdjunto(loteId(), a.id).subscribe()" [disabled]="store.ocupado()" [attr.aria-label]="'Quitar ' + a.nombre"
                            class="shrink-0 text-muted hover:text-red-700 disabled:opacity-50">✕</button>
                        }
                      </li>
                    }
                  </ul>
                } @else {
                  <p class="text-muted">Ninguno todavía.</p>
                }
                @if (editar()) {
                  <label class="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-line bg-white px-2.5 py-1 font-semibold hover:border-brand-300 focus-within:ring-2 focus-within:ring-brand-400">
                    Adjuntar archivo…
                    <input type="file" multiple class="sr-only" [attr.accept]="extensiones()" (change)="elegir($event)" [disabled]="store.ocupado()" />
                  </label>
                }
              </div>
            </div>
          }
        }
      </td>
    </tr>
  `,
})
export class FilaPartida {
  protected readonly store = inject(LotesStore);
  protected readonly estados = ESTADO_FILA;
  protected readonly pesos = pesos;
  protected readonly peso = peso;

  readonly partida = input.required<Partida>({ alias: 'appFilaPartida' });
  readonly estado = input.required<EstadoFila>();
  /** Los comprobantes pegados a esta partida. */
  readonly adjuntos = input<Adjunto[]>([]);
  readonly loteId = input.required<string>();
  readonly version = input.required<number>();
  readonly editar = input(false);

  protected readonly panel = linkedSignal<string, Panel>({ source: () => this.partida().clave, computation: () => null });
  protected readonly motivo = signal('');
  protected readonly borrador = signal({ justificacion: '', comentario: '', importe: '' });
  protected readonly encima = signal(false);
  protected readonly subiendo = signal(false);
  protected readonly rechazo = signal<string | null>(null);

  protected readonly diferencia = computed(() => diferenciaFila(this.partida()));
  protected readonly justificaciones = computed(() => Object.entries(this.store.catalogo()?.justificaciones ?? {}));
  protected readonly justificacion = computed(() => {
    const j = this.partida().justificacion;
    return this.store.catalogo()?.justificaciones[j] ?? j;
  });
  protected readonly extensiones = computed(() => (this.store.catalogo()?.adjuntos_permitidos ?? ['.pdf', '.jpg', '.jpeg', '.png']).join(','));
  protected readonly importeInvalido = computed(() => !!this.borrador().importe.trim() && leerImporte(this.borrador().importe) === null);

  protected readonly motivos = computed(() => {
    const p = this.partida();
    const catalogo = this.store.catalogo();
    const motivo = MOTIVOS[p.codigo] ?? { texto: p.estado, clase: OBSERVADA };
    const lista = [{ ...motivo, ayuda: catalogo?.codigos[p.codigo] ?? p.estado }];
    if (p.flags.includes('FUERA_PER')) lista.push({ texto: 'Fuera de período', clase: OBSERVADA, ayuda: catalogo?.flags['FUERA_PER'] ?? '' });
    return lista;
  });

  protected boton(panel: Panel): string {
    const activo = panel !== null && this.panel() === panel;
    return (
      'relative flex h-7 w-7 items-center justify-center rounded-md border transition-colors disabled:cursor-not-allowed disabled:opacity-40 ' +
      (activo ? 'border-brand-700 bg-brand-700 text-white' : 'border-line bg-white text-ink hover:border-brand-300 hover:text-brand-700')
    );
  }

  protected abrir(panel: Panel): void {
    if (this.panel() === panel) return this.panel.set(null);
    const p = this.partida();
    if (panel === 'ajustar') this.borrador.set({ justificacion: p.justificacion, comentario: p.comentario, importe: p.importe_ajustado ?? '' });
    if (panel === 'descartar') this.motivo.set(p.comentario);
    this.rechazo.set(null);
    this.panel.set(panel);
  }

  protected campo(nombre: 'justificacion' | 'comentario' | 'importe', valor: string): void {
    this.borrador.update((b) => ({ ...b, [nombre]: valor }));
  }

  protected guardarAjuste(): void {
    const b = this.borrador();
    const p = this.partida();
    const cambios: CambioPartida = { justificacion: b.justificacion, comentario: b.comentario.trim() };
    const importe = b.importe.trim() ? leerImporte(b.importe) : null;
    if (importe !== p.importe_ajustado) cambios.importe_ajustado = importe;
    this.guardar(cambios);
  }

  protected descartar(): void {
    this.guardar({ excluida: true, comentario: this.motivo().trim() });
  }

  protected restaurar(): void {
    this.guardar({ excluida: false });
  }

  private guardar(cambios: CambioPartida): void {
    this.store.actualizarPartida(this.loteId(), this.partida().clave, this.version(), cambios).subscribe((ok) => ok && this.panel.set(null));
  }

  // --- Soltar un comprobante sobre la fila -----------------------------------

  protected sobre(evento: DragEvent): void {
    if (!this.editar() || !evento.dataTransfer?.types.includes('Files')) return;
    evento.preventDefault();
    evento.dataTransfer.dropEffect = 'copy';
    this.encima.set(true);
  }

  /** Pasar por encima de una celda también dispara dragleave: solo cuenta si sale de la fila. */
  protected salir(evento: DragEvent): void {
    const fila = evento.currentTarget as HTMLElement;
    if (!fila.contains(evento.relatedTarget as Node | null)) this.encima.set(false);
  }

  protected soltar(evento: DragEvent): void {
    if (!this.editar()) return;
    evento.preventDefault();
    this.encima.set(false);
    this.adjuntar(evento.dataTransfer?.files);
  }

  protected elegir(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    this.adjuntar(entrada.files);
    entrada.value = '';
  }

  private adjuntar(lista: FileList | null | undefined): void {
    const archivos = Array.from(lista ?? []);
    if (!archivos.length || this.store.ocupado()) return;
    const permitidas = this.extensiones().split(',');
    const malos = archivos.filter((a) => !permitidas.includes(a.name.slice(a.name.lastIndexOf('.')).toLowerCase()));
    this.rechazo.set(malos.length ? `No se adjuntó ${malos.map((a) => a.name).join(', ')}: tipo de archivo no permitido.` : null);
    const buenos = archivos.filter((a) => !malos.includes(a));
    // De a uno: cada subida devuelve el lote entero y la siguiente parte de ahí.
    const siguiente = (i: number): void => {
      if (i >= buenos.length) return this.subiendo.set(false);
      this.store.adjuntarAPartida(this.loteId(), this.partida().clave, buenos[i]).subscribe((ok) => (ok ? siguiente(i + 1) : this.subiendo.set(false)));
    };
    if (buenos.length) {
      this.subiendo.set(true);
      siguiente(0);
    }
  }
}
