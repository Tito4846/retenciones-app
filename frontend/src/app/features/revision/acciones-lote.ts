import { Component, computed, inject, input, linkedSignal, output } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { Permisos } from './revision.util';

/** Pie fijo: los botones que mueven el lote, según quién mira y en qué estado está. */
@Component({
  selector: 'app-acciones-lote',
  host: { class: 'block border-t border-line bg-white p-3' },
  template: `
    @let lote = detalle().lote;
    @let p = permisos();
    @if (confirmar(); as tipo) {
      <div class="mb-3 rounded-lg border px-4 py-3" [class]="tipo === 'aprobar' ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'">
        @if (tipo === 'aprobar') {
          <p class="text-sm text-emerald-900">
            Vas a aprobar la versión {{ lote.version_vigente }} de <strong>{{ lote.empresa }}</strong>.
            Queda cerrada: no se va a poder editar ni borrar nada, y su Excel queda como el definitivo.
          </p>
          @if (aMirar().length) {
            <p class="mt-1 text-sm font-semibold text-amber-800">Se aprueba con esto sin resolver: {{ aMirar().join(' · ') }}. Queda registrado.</p>
          }
        } @else if (tipo === 'eliminar') {
          <p class="text-sm text-red-900">
            Vas a borrar <strong>{{ lote.empresa }}</strong> del todo: versiones, partidas, comprobantes y exportaciones. No se puede deshacer.
            En la auditoría queda solo que lo eliminaste.
          </p>
        } @else {
          <label class="block text-sm">
            <span class="font-semibold">{{ tipo === 'rechazar' ? 'Qué tiene que corregir ' + lote.maker_nombre : 'Por qué se anula' }}</span>
            <textarea rows="3" [value]="motivo()" (input)="motivo.set($any($event.target).value)"
              class="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 outline-none focus:border-brand-400"
              placeholder="Ej.: La diferencia de $15.000 del certificado 4512 no tiene la nota de crédito"></textarea>
          </label>
          <p class="text-xs text-muted">Al menos {{ minimo() }} caracteres.</p>
        }
        <div class="mt-2 flex justify-end gap-2">
          <button type="button" (click)="confirmar.set(null)" class="rounded-lg px-3 py-2 text-sm font-semibold text-muted hover:bg-white">Cancelar</button>
          <button type="button" (click)="confirmarAccion()" [disabled]="store.ocupado() || ((tipo === 'rechazar' || tipo === 'anular') && motivo().trim().length < minimo())"
            class="rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            [class]="tipo === 'aprobar' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700'">
            {{ tipo === 'aprobar' ? 'Sí, aprobar y cerrar' : tipo === 'rechazar' ? 'Devolver' : tipo === 'eliminar' ? 'Sí, eliminar' : 'Anular lote' }}
          </button>
        </div>
      </div>
    }

    @if (p.editar && !p.revisar) {
      <input type="text" [value]="nota()" (input)="nota.set($any($event.target).value)" aria-label="Nota para quien revisa"
        placeholder="Nota para quien revisa (opcional)"
        class="mb-2 w-full rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-brand-400" />
    }
    <div class="flex flex-wrap items-center justify-end gap-2">
      <p class="mr-auto min-w-[12rem] flex-1 text-sm" [class]="trabas().length || aMirar().length ? 'text-amber-800' : 'text-muted'">{{ leyenda() }}</p>
      @if (p.eliminar) {
        <button type="button" (click)="confirmar.set('eliminar')" class="px-2 text-sm font-semibold text-red-700 hover:underline">Eliminar lote</button>
      }
      @if (p.anular) {
        <button type="button" (click)="confirmar.set('anular')" class="px-2 text-sm font-semibold text-muted hover:text-red-700">Anular</button>
      }
      @if (p.editar && !p.revisar) {
        <!-- Lo principal para quien prepara: el botón más grande y con más peso de la barra -->
        <span [title]="trabasEnviar().join('\\n')">
          <button type="button" (click)="enviar()" [disabled]="store.ocupado() || trabasEnviar().length > 0"
            class="flex items-center gap-2 rounded-lg bg-brand-700 px-5 py-2.5 text-sm font-bold text-white shadow-md shadow-brand-700/25 ring-2 ring-brand-700/20 ring-offset-2 transition hover:bg-brand-800 hover:shadow-lg focus-visible:ring-brand-400 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none">
            {{ lote.estado === 'rechazado' ? 'Reenviar a Revisión' : 'Enviar a Revisión' }}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
          </button>
        </span>
      }
      @if (p.tomar) {
        <button type="button" (click)="store.tomar(lote.id).subscribe()" [disabled]="store.ocupado()"
          class="rounded-lg bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-800 disabled:opacity-50">Tomar para revisar</button>
      }
      @if (p.reasignar) {
        <button type="button" (click)="store.tomar(lote.id).subscribe()" [disabled]="store.ocupado()"
          class="rounded-lg bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-800 disabled:opacity-50">Pasármelo a mí</button>
      }
      @if (p.revisar) {
        <button type="button" (click)="confirmar.set('rechazar')" [disabled]="store.ocupado()"
          class="rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">Devolver para Corregir</button>
      }
      @if (p.revisar || p.autor) {
        @let bloqueado = trabasAprobar().length > 0;
        <!--
          Poka-yoke: deshabilitado con diferencia neta, excepciones sin completar o lote propio.
          Un botón disabled no recibe foco ni hover: el envoltorio hace de disparador del tooltip.
        -->
        <span class="group relative inline-flex" [attr.tabindex]="bloqueado ? 0 : null" [attr.aria-describedby]="bloqueado ? 'por-que-no-aprobar' : null">
          <button type="button" (click)="confirmar.set('aprobar')" [disabled]="store.ocupado() || bloqueado"
            class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:pointer-events-none disabled:bg-zinc-300 disabled:text-zinc-600">Aprobar y Cerrar Lote</button>
          @if (bloqueado) {
            <span id="por-que-no-aprobar" role="tooltip"
              class="pointer-events-none invisible absolute right-0 bottom-full z-40 mb-2 w-72 rounded-lg bg-ink px-3 py-2 text-left text-xs text-white opacity-0 shadow-lg transition group-hover:visible group-hover:opacity-100 group-focus:visible group-focus:opacity-100">
              <span class="block font-semibold">Todavía no se puede cerrar el lote:</span>
              @for (m of trabasAprobar(); track m) { <span class="mt-1 block">• {{ m }}</span> }
              <span class="absolute top-full right-6 border-4 border-transparent border-t-ink" aria-hidden="true"></span>
            </span>
          }
        </span>
      }
    </div>
  `,
})
export class AccionesLote {
  protected readonly store = inject(LotesStore);

  readonly detalle = input.required<DetalleLote>();
  readonly permisos = input.required<Permisos>();
  readonly trabasEnviar = input.required<string[]>();
  readonly trabasAprobar = input.required<string[]>();
  /** Lo que quedó sin resolver: se informa, no frena. */
  readonly pendiente = input<string[]>([]);
  readonly eliminado = output<void>();

  private readonly loteId = computed(() => this.detalle().lote.id);
  protected readonly confirmar = linkedSignal<string, 'rechazar' | 'anular' | 'aprobar' | 'eliminar' | null>({ source: this.loteId, computation: () => null });
  protected readonly motivo = linkedSignal({ source: this.loteId, computation: () => '' });
  protected readonly nota = linkedSignal({ source: this.loteId, computation: () => '' });
  protected readonly minimo = computed(() => this.store.catalogo()?.motivo_minimo ?? 15);

  protected readonly trabas = computed(() => {
    const p = this.permisos();
    return p.revisar ? this.trabasAprobar() : p.editar ? this.trabasEnviar() : [];
  });

  /** Se muestra a quien puede enviar o aprobar: a los demás no les cambia nada. */
  protected readonly aMirar = computed(() => (this.permisos().revisar || this.permisos().editar ? this.pendiente() : []));

  /** A la izquierda de los botones: qué falta, o en qué está el lote si no te toca a vos. */
  protected readonly leyenda = computed(() => {
    const lote = this.detalle().lote;
    const p = this.permisos();
    if (this.trabas().length) return `Para seguir: ${this.trabas().join(' · ')}`;
    const mirar = this.aMirar().join(' · ');
    if (p.revisar) return mirar ? `Para evaluar: ${mirar}. Decidís vos si lo aprobás.` : 'Todo controlado: lo podés aprobar.';
    if (p.editar) return mirar ? `Queda: ${mirar}. Lo podés enviar igual.` : 'Todo completo: ya lo podés enviar.';
    if (p.tomar) return `Lo preparó ${lote.maker_nombre}. Al tomarlo queda a tu nombre.`;
    if (p.reasignar) return `Lo está revisando ${lote.checker_nombre}.`;
    switch (lote.estado) {
      case 'borrador': return `${lote.maker_nombre} lo está preparando.`;
      case 'rechazado': return `Se le devolvió a ${lote.maker_nombre} para corregir.`;
      case 'pendiente': return 'Esperando que lo tome un supervisor.';
      case 'en_revision': return `Lo está revisando ${lote.checker_nombre}.`;
      case 'aprobado':
        return `Aprobado${lote.checker_nombre ? ' por ' + lote.checker_nombre : ''}. Su Excel es el definitivo.`;
      default: return 'Anulado. Queda solo como registro.';
    }
  });

  protected enviar(): void {
    const { id, version_vigente } = this.detalle().lote;
    this.store.enviar(id, version_vigente, this.nota()).subscribe((ok) => ok && this.nota.set(''));
  }

  protected confirmarAccion(): void {
    const tipo = this.confirmar();
    const { id, version_vigente } = this.detalle().lote;
    const fin = (ok: boolean) => ok && (this.confirmar.set(null), this.motivo.set(''));
    if (tipo === 'rechazar') this.store.rechazar(id, version_vigente, this.motivo()).subscribe(fin);
    if (tipo === 'anular') this.store.anular(id, this.motivo()).subscribe(fin);
    if (tipo === 'eliminar') this.store.eliminar(id).subscribe((ok) => ok && this.eliminado.emit());
    if (tipo === 'aprobar') {
      this.store.aprobar(id, version_vigente).subscribe(fin);
    }
  }
}
