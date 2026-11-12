import { Component, computed, effect, inject, model, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EstadoLote, LoteListado } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { SesionService } from '../../core/services/sesion.service';
import { progreso } from './revision.reglas';
import { CONCILIADO, ESTADOS, hace, iniciales, periodo, pesos, quienArmo } from './revision.util';

type Bandeja = 'mias' | 'espera' | 'cerrados';

const PESTANAS: { id: Bandeja; titulo: string }[] = [
  { id: 'mias', titulo: 'Mis Tareas' },
  { id: 'espera', titulo: 'Esperando Revisión' },
  { id: 'cerrados', titulo: 'Cerrados' },
];

/** Columna izquierda: la bandeja de lotes. Elegir uno lo abre en el panel de la derecha. */
@Component({
  selector: 'app-bandeja-lotes',
  imports: [RouterLink],
  host: { class: 'flex min-h-0 flex-col rounded-2xl border border-line bg-white' },
  template: `
    <div class="space-y-2 border-b border-line p-3">
      <div class="flex items-baseline justify-between gap-2 px-1">
        <h2 class="text-sm font-semibold">{{ supervisor() ? 'Solicitudes de revisión' : 'Mis lotes' }}</h2>
        @if (supervisor() && nuevas()) {
          <span class="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900 tabular-nums">{{ nuevas() }} {{ nuevas() === 1 ? 'nueva' : 'nuevas' }}</span>
        }
      </div>
      <div class="grid grid-cols-3 gap-1 rounded-lg bg-canvas p-1 text-xs font-semibold" role="tablist" aria-label="Bandejas">
        @for (t of pestanas; track t.id) {
          <button type="button" role="tab" [attr.aria-selected]="bandeja() === t.id" (click)="elegida.set(t.id)"
            class="flex flex-col items-center justify-center gap-0.5 rounded-md px-1 py-1.5 leading-tight"
            [class]="bandeja() === t.id ? 'bg-white text-ink shadow-sm' : 'text-muted hover:text-ink'">
            {{ t.titulo }}
            <span class="rounded-full px-1.5 tabular-nums"
              [class]="t.id === 'mias' && cantidades().mias ? 'bg-amber-500 text-white' : 'bg-line/60 text-muted'">{{ cantidades()[t.id] }}</span>
          </button>
        }
      </div>
      <input type="search" [value]="busqueda()" (input)="busqueda.set($any($event.target).value)" aria-label="Buscar lotes"
        placeholder="Buscar cliente, CUIT, período o persona…"
        class="w-full rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-brand-400" />
    </div>
    <ul class="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
      @for (l of visibles(); track l.id) {
        @let paraMi = store.esperaA(l, yo());
        <li>
          <button type="button" (click)="seleccion.set(l.id)" class="w-full border-l-4 px-4 py-3 text-left transition-colors"
            [attr.aria-current]="store.detalle()?.lote?.id === l.id ? 'true' : null"
            [class]="(store.detalle()?.lote?.id === l.id ? 'bg-brand-50 ' : 'hover:bg-canvas ') + (paraMi ? 'border-amber-400' : 'border-transparent')">
            <div class="flex items-start justify-between gap-2">
              <p class="min-w-0 truncate font-semibold" [title]="l.empresa">{{ l.empresa }}</p>
              <span class="shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ring-1" [class]="estados[l.estado].clase">{{ estados[l.estado].titulo }}</span>
            </div>
            <p class="mt-1 truncate text-xs text-muted">
              @if (l.cuit_empresa) { CUIT {{ l.cuit_empresa }} · }{{ periodo(l.periodo) }}
            </p>
            <div class="mt-1 flex items-baseline justify-between gap-2">
              <p class="min-w-0 truncate text-xs text-muted" [title]="impuestos(l)">{{ impuestos(l) }}</p>
              <p class="shrink-0 font-semibold tabular-nums">{{ pesos(monto(l)) }}</p>
            </div>
            @if (l.autor_version) {
              <div class="mt-1.5 flex items-center gap-1.5 text-xs">
                <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-100 text-[10px] font-bold text-brand-700" aria-hidden="true">{{ iniciales(l.autor_version) }}</span>
                <p class="min-w-0 truncate"><span class="font-semibold">{{ quien(l) }}</span> <span class="text-muted">· {{ cuando(l) }}</span></p>
              </div>
            }
            @if (excepciones(l); as n) {
              @let avance = progreso(n, l.sin_completar);
              <div class="mt-2 flex items-center gap-2" role="progressbar" [attr.aria-valuenow]="avance" aria-valuemin="0" aria-valuemax="100"
                [attr.aria-label]="'Excepciones resueltas de ' + l.empresa">
                <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
                  <div class="h-full rounded-full transition-[width] duration-500" [style.width.%]="avance"
                    [class]="avance === 100 ? 'bg-emerald-500' : avance >= 50 ? 'bg-amber-500' : 'bg-red-500'"></div>
                </div>
                <span class="w-9 shrink-0 text-right text-[11px] font-semibold text-slate-600 tabular-nums">{{ avance }}%</span>
              </div>
              <p class="mt-1 text-xs">
                <span class="font-semibold" [class]="l.sin_completar ? 'text-amber-800' : 'text-emerald-700'">
                  {{ n }} {{ n === 1 ? 'excepción' : 'excepciones' }}{{ l.sin_completar ? ' · ' + l.sin_completar + ' sin completar' : ' · completas' }}
                </span>
                @if (l.comprobantes) { <span class="text-muted"> · {{ l.comprobantes === 1 ? '1 comprobante' : l.comprobantes + ' comprobantes' }}</span> }
              </p>
            }
            <p class="mt-1 truncate text-xs" [class]="paraMi ? 'font-semibold text-amber-800' : 'text-muted'">
              {{ situacion(l) }}
              @if (l.observaciones_abiertas) {
                <span class="font-semibold text-red-700">· {{ l.observaciones_abiertas }} observación(es)</span>
              }
            </p>
          </button>
        </li>
      } @empty {
        <li class="px-4 py-10 text-center text-sm text-muted">
          @if (store.cargando()) {
            Cargando…
          } @else if (bandeja() === 'mias' && store.lotes().length) {
            No tenés nada pendiente.
          } @else if (store.lotes().length) {
            No hay lotes acá.
          } @else {
            Todavía no hay lotes. Se arman solos al terminar una conciliación en <a routerLink="/importar" class="font-semibold text-brand-700 underline">Importar</a>.
          }
        </li>
      }
    </ul>
  `,
})
export class BandejaLotes {
  protected readonly store = inject(LotesStore);
  private readonly sesion = inject(SesionService);
  protected readonly estados = ESTADOS;
  protected readonly pestanas = PESTANAS;
  protected readonly pesos = pesos;
  protected readonly periodo = periodo;
  protected readonly iniciales = iniciales;
  /** (total_excepciones − sin_completar) / total_excepciones. */
  protected readonly progreso = progreso;

  /** El lote elegido. null = todavía ninguno: se abre el primero de la lista. */
  readonly seleccion = model<string | null>(null);

  /** null = la elige sola: «Mis tareas» si hay algo. */
  protected readonly elegida = signal<Bandeja | null>(null);
  protected readonly bandeja = computed<Bandeja>(() => this.elegida() ?? (this.cantidades().mias ? 'mias' : 'espera'));
  protected readonly busqueda = signal('');
  protected readonly yo = computed(() => this.sesion.usuario()?.id ?? '');
  protected readonly supervisor = this.sesion.esSupervisor;

  protected readonly cantidades = computed(() => {
    const cuenta = { mias: 0, espera: 0, cerrados: 0 };
    for (const l of this.store.lotes()) cuenta[this.lugar(l)]++;
    return cuenta;
  });

  /** Solicitudes que todavía no tomó nadie y que no preparó este supervisor. */
  protected readonly nuevas = computed(() => {
    const yo = this.yo();
    return this.store.lotes().filter((l) => l.estado === 'pendiente' && !l.autores.includes(yo)).length;
  });

  protected readonly visibles = computed(() => {
    const texto = this.busqueda().trim().toLowerCase();
    const bandeja = this.bandeja();
    return this.store.lotes().filter(
      (l) =>
        this.lugar(l) === bandeja &&
        (!texto || `${l.empresa} ${l.cuit_empresa} ${l.periodo} ${l.maker_nombre} ${l.autor_version ?? ''} ${l.checker_nombre ?? ''}`.toLowerCase().includes(texto)),
    );
  });

  constructor() {
    // Por usuario: al cambiar de cuenta se vuelve a pedir con sus permisos.
    let abierto: string | undefined;
    effect(() => {
      const id = this.seleccion() ?? this.visibles()[0]?.id ?? null;
      const clave = `${this.yo()}:${id}`;
      if (clave === abierto) return;
      abierto = clave;
      untracked(() => this.store.abrir(id));
    });
  }

  /**
   * Cada lote está en una sola bandeja: lo que ya es tuyo para mover, lo que
   * espera (incluidos los enviados que todavía no tomó ningún supervisor) y lo
   * cerrado.
   */
  private lugar(l: LoteListado): Bandeja {
    if (this.cerrado(l.estado)) return 'cerrados';
    return this.store.enMisTareas(l, this.yo()) ? 'mias' : 'espera';
  }

  private cerrado(estado: EstadoLote): boolean {
    return estado === 'aprobado' || estado === 'anulado';
  }

  protected impuestos(l: LoteListado): string {
    return l.impuestos?.split(',').join(', ') || 'Sin impuesto';
  }

  protected monto(l: LoteListado): string {
    return l.resumen.importe_declarado ?? l.resumen.importe_fisco ?? l.total_fisco;
  }

  protected excepciones(l: LoteListado): number {
    const { excepciones, por_estado } = l.resumen;
    if (excepciones !== undefined) return excepciones;
    return Object.entries(por_estado ?? {}).reduce((n, [estado, cuantas]) => (estado === CONCILIADO ? n : n + cuantas), 0);
  }

  protected quien(l: LoteListado): string {
    return quienArmo(l.autor_version ?? '', l.origen_version ?? '', l.version_correccion ?? 0);
  }

  /** Si ya se envió, cuánto hace que espera; si no, cuánto hace que se tocó. */
  protected cuando(l: LoteListado): string {
    return l.estado === 'pendiente' && l.ultimo_envio ? `enviado ${hace(l.ultimo_envio)}` : hace(l.version_utc);
  }

  /** En una línea, en qué está el lote y quién tiene que moverlo. */
  protected situacion(l: LoteListado): string {
    const yo = this.yo();
    switch (l.estado) {
      case 'borrador':
        return l.maker_id === yo ? 'Te toca completarlo y enviarlo' : `Lo está preparando ${l.maker_nombre}`;
      case 'pendiente':
        return l.autores.includes(yo) ? 'Esperando que lo tome un supervisor' : 'Listo para que alguien lo revise';
      case 'en_revision':
        return l.checker_id === yo ? 'Lo estás revisando vos' : `Lo está revisando ${l.checker_nombre}`;
      case 'rechazado':
        return l.maker_id === yo ? 'Te lo devolvieron: hay que corregir' : `Devuelto a ${l.maker_nombre}`;
      case 'aprobado':
        return `Aprobado${l.checker_nombre ? ' por ' + l.checker_nombre : ''}`;
      default:
        return 'Anulado';
    }
  }
}
