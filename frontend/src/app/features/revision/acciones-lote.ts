import { Component, computed, inject, input, linkedSignal, model, output } from '@angular/core';
import { DetalleLote } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { Permisos } from './revision.util';

const SIN_AUTOAPROBACION = 'No podés aprobar un lote que vos mismo preparaste';

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
            Queda cerrada: no se va a poder editar ni borrar nada, y se habilita la exportación para la DDJJ.
            @if (evidencia().length; as n) { Se {{ n === 1 ? 'adjunta 1 comprobante' : 'adjuntan ' + n + ' comprobantes' }} al cierre. }
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

    @if (p.exportar) {
      <div class="flex flex-wrap items-center gap-2">
        <span class="mr-auto text-sm font-semibold">Exportación para la DDJJ</span>
        @for (x of exportaciones(); track x[0]) {
          <button type="button" (click)="store.exportar(lote.id, x[0])" class="rounded-lg bg-brand-700 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-800">{{ x[1] }}</button>
        }
        @if (p.eliminar) {
          <button type="button" (click)="confirmar.set('eliminar')" class="px-2 text-sm font-semibold text-red-700 hover:underline">Eliminar lote</button>
        }
      </div>
      @if (detalle().exportaciones.length) {
        <p class="mt-2 flex flex-wrap gap-2 text-sm">
          <span class="text-muted">Ya generadas:</span>
          @for (e of detalle().exportaciones; track e.id) {
            <button type="button" (click)="store.bajarExportacion(lote.id, e.id, e.nombre)" class="font-semibold text-brand-700 underline">{{ e.nombre }}</button>
          }
        </p>
      }
    } @else {
      @if (p.editar && evidencia().length) {
        <p class="mb-2 text-xs text-muted">
          {{ evidencia().length === 1 ? '1 comprobante espera' : evidencia().length + ' comprobantes esperan' }} la confirmación: se {{ evidencia().length === 1 ? 'adjunta' : 'adjuntan' }} al {{ p.revisar ? 'aprobar' : 'enviar' }}.
        </p>
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
          <span [title]="trabasEnviar().join('\\n')">
            <button type="button" (click)="enviar()" [disabled]="store.ocupado() || trabasEnviar().length > 0"
              class="rounded-lg bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-50">
              {{ lote.estado === 'rechazado' ? 'Reenviar a Revisión' : 'Enviar a Revisión' }}
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
          <span [title]="p.autor ? sinAutoaprobacion : trabasAprobar().join('\\n')">
            <button type="button" (click)="confirmar.set('aprobar')" [disabled]="store.ocupado() || p.autor || trabasAprobar().length > 0"
              class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50">Aprobar y Cerrar Lote</button>
          </span>
        }
      </div>
    }
  `,
})
export class AccionesLote {
  protected readonly store = inject(LotesStore);
  protected readonly sinAutoaprobacion = SIN_AUTOAPROBACION;

  readonly detalle = input.required<DetalleLote>();
  readonly permisos = input.required<Permisos>();
  readonly trabasEnviar = input.required<string[]>();
  readonly trabasAprobar = input.required<string[]>();
  /** Lo que quedó sin resolver: se informa, no frena. */
  readonly pendiente = input<string[]>([]);
  /** Se manda con la confirmación y se vacía solo si salió bien. */
  readonly evidencia = model<File[]>([]);
  readonly eliminado = output<void>();

  private readonly loteId = computed(() => this.detalle().lote.id);
  protected readonly confirmar = linkedSignal<string, 'rechazar' | 'anular' | 'aprobar' | 'eliminar' | null>({ source: this.loteId, computation: () => null });
  protected readonly motivo = linkedSignal({ source: this.loteId, computation: () => '' });
  protected readonly nota = linkedSignal({ source: this.loteId, computation: () => '' });
  protected readonly minimo = computed(() => this.store.catalogo()?.motivo_minimo ?? 15);
  protected readonly exportaciones = computed(() => Object.entries(this.store.catalogo()?.exportaciones ?? {}));

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
      default: return 'Anulado. Queda solo como registro.';
    }
  });

  protected enviar(): void {
    const { id, version_vigente } = this.detalle().lote;
    this.store.enviar(id, version_vigente, this.nota(), this.evidencia()).subscribe((ok) => {
      if (!ok) return;
      this.nota.set('');
      this.evidencia.set([]);
    });
  }

  protected confirmarAccion(): void {
    const tipo = this.confirmar();
    const { id, version_vigente } = this.detalle().lote;
    const fin = (ok: boolean) => ok && (this.confirmar.set(null), this.motivo.set(''));
    if (tipo === 'rechazar') this.store.rechazar(id, version_vigente, this.motivo()).subscribe(fin);
    if (tipo === 'anular') this.store.anular(id, this.motivo()).subscribe(fin);
    if (tipo === 'eliminar') this.store.eliminar(id).subscribe((ok) => ok && this.eliminado.emit());
    if (tipo === 'aprobar') {
      this.store.aprobar(id, version_vigente, this.evidencia()).subscribe((ok) => {
        if (ok) this.evidencia.set([]);
        fin(ok);
      });
    }
  }
}
