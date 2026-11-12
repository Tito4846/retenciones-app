import { Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EventoBot } from '../../core/models/conexiones.models';
import { ConfirmModal } from '../../core/ui/confirm-modal';
import { BADGE, cuandoLegible, hora, SaludBot } from './conexion-bot.reglas';

/**
 * La tarjeta de un bot, solo presentación: no conoce el store ni la API. Pinta el
 * estado, ordena los botones por importancia y pide confirmación antes de desconectar.
 * El formulario de credenciales o el detalle de la conexión entran por <ng-content>.
 *
 * Jerarquía de botones:
 *  1. Principal (sólido): lo que el bot hace solo («Consultar retenciones»).
 *  2. Manual (outline): abrir la ventana del bot. Si el bot espera a la persona
 *     (requiere acción), pasa a ser el principal: es lo único que destraba.
 *  3. Destructiva (texto rojo): desconectar, con confirmación.
 */
@Component({
  selector: 'app-bot-connection-card',
  imports: [ConfirmModal, RouterLink],
  host: { class: 'flex flex-col rounded-2xl border bg-white p-6 transition-colors' , '[class]': 'borde()' },
  template: `
    @let b = badge();
    <header class="mb-4 flex items-start justify-between gap-3">
      <div class="min-w-0">
        <h2 class="text-lg font-semibold">{{ nombre() }}</h2>
        <p class="text-sm text-muted">{{ descripcion() }}</p>
      </div>
      <div class="flex shrink-0 flex-col items-end gap-1" role="status" aria-live="polite">
        <span class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset" [class]="b.clase">
          <span class="h-2 w-2 rounded-full" [class]="b.punto" aria-hidden="true"></span>{{ etiqueta() ?? b.texto }}
        </span>
        @if (ultimaActividad()) {
          <span class="text-xs text-slate-500 tabular-nums" [title]="ultimaActividad()">Última actividad: {{ cuando() }}</span>
        }
      </div>
    </header>

    @if (mensaje() && (salud() === 'error' || salud() === 'requiere_accion')) {
      <div class="mb-4 flex items-start gap-2 rounded-lg px-3 py-2 text-sm"
        [class]="salud() === 'error' ? 'bg-red-50 text-red-800' : 'bg-amber-50 text-amber-900'" role="alert">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true">
          <circle cx="12" cy="12" r="9" /><path d="M12 8v4m0 4h.01" />
        </svg>
        <p>
          @if (salud() === 'error') { <span class="font-semibold">No hay conexión. </span> }
          {{ mensaje() }}
        </p>
      </div>
    } @else if (salud() === 'en_curso' && mensaje()) {
      <div class="mb-4 flex items-center gap-3 text-sm text-muted">
        <span class="h-5 w-5 shrink-0 animate-spin rounded-full border-[3px] border-brand-100 border-t-brand-600" aria-hidden="true"></span>
        {{ mensaje() }}
      </div>
    }

    <ng-content />

    @if (hayAcciones()) {
      <div class="mt-4 flex flex-wrap items-center gap-3">
        @if (principal(); as p) {
          @if (p.ruta) {
            <a [routerLink]="p.ruta" class="inline-flex items-center gap-2 rounded-lg bg-brand-700 px-5 py-2.5 text-sm font-bold text-white shadow-md shadow-brand-700/20 hover:bg-brand-800 focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:outline-none">
              {{ p.texto }}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
            </a>
          }
        }
        @if (manual(); as m) {
          <button type="button" (click)="abrirVentana.emit()" [disabled]="ocupado()"
            class="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
            [class]="manualPrincipal()
              ? 'bg-amber-500 text-white shadow-md hover:bg-amber-600'
              : 'border border-line bg-transparent text-ink hover:border-brand-300 hover:bg-brand-50/50'">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18" />
            </svg>
            {{ m }}
          </button>
        }
        @if (desconectar(); as d) {
          <button type="button" (click)="pedirDesconexion()" [disabled]="ocupado()"
            class="ml-auto rounded-lg border border-transparent px-3 py-2 text-sm font-semibold text-red-700 hover:border-red-300 hover:bg-red-50 disabled:opacity-50">
            {{ d }}
          </button>
        }
      </div>
    }

    @if (eventos().length) {
      <section class="mt-5 rounded-xl border border-line bg-canvas" [attr.aria-label]="'Actividad del bot de ' + nombre()">
        <p class="border-b border-line px-4 py-2 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">Actividad del bot</p>
        <ol class="max-h-32 divide-y divide-line/70 overflow-y-auto px-4 text-sm" aria-live="polite">
          @for (e of eventos(); track e.cuando + e.mensaje) {
            <li class="flex items-start gap-2 py-1.5">
              @switch (e.tipo) {
                @case ('ok') {
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" role="img" aria-label="Éxito"><path d="M20 6 9 17l-5-5" /></svg>
                }
                @case ('error') {
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="mt-0.5 h-4 w-4 shrink-0 text-red-600" role="img" aria-label="Error"><path d="M18 6 6 18M6 6l12 12" /></svg>
                }
                @default {
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" class="mt-0.5 h-4 w-4 shrink-0 text-slate-400" role="img" aria-label="Paso"><circle cx="12" cy="12" r="3" fill="currentColor" /></svg>
                }
              }
              <p class="min-w-0 flex-1" [class]="e.tipo === 'error' ? 'text-red-800' : 'text-ink'">{{ e.mensaje }}</p>
              <time class="shrink-0 text-xs text-slate-500 tabular-nums" [attr.datetime]="e.cuando">{{ hora(e.cuando) }}</time>
            </li>
          }
        </ol>
      </section>
    }

    <app-confirm-modal [(abierto)]="confirmando" [titulo]="'¿Desconectar ' + nombre() + '?'" textoConfirmar="Sí, desconectar" [peligro]="true"
      (confirmado)="desconectado.emit()">
      Se cierra la ventana del bot y <strong class="text-ink">se detiene la automatización</strong>: {{ advertencia() }}
      Para volver a usarlo vas a tener que conectarte de nuevo.
    </app-confirm-modal>
  `,
})
export class BotConnectionCard {
  protected readonly hora = hora;

  readonly nombre = input.required<string>();
  readonly descripcion = input('');
  readonly salud = input.required<SaludBot>();
  /** Reemplaza el texto del badge sin cambiar el color («Renovando sesión…»). */
  readonly etiqueta = input<string | null>(null);
  readonly mensaje = input<string | null>(null);
  readonly ultimaActividad = input<string | null | undefined>(null);
  readonly eventos = input<EventoBot[]>([]);
  readonly ocupado = input(false);

  /** La acción de valor. Solo se muestra conectado. */
  readonly principal = input<{ texto: string; ruta: string } | null>(null);
  /** Texto del botón que trae la ventana del bot. null = sin botón. */
  readonly manual = input<string | null>(null);
  /** «Desconectar» o «Cancelar». null = sin botón. */
  readonly desconectar = input<string | null>(null);
  /** Qué deja de pasar al desconectar, para el modal. */
  readonly advertencia = input('');

  readonly abrirVentana = output<void>();
  readonly desconectado = output<void>();

  protected readonly confirmando = signal(false);
  protected readonly badge = computed(() => BADGE[this.salud()]);
  protected readonly cuando = computed(() => cuandoLegible(this.ultimaActividad()));
  protected readonly manualPrincipal = computed(() => this.salud() === 'requiere_accion');
  protected readonly hayAcciones = computed(() => !!(this.principal() || this.manual() || this.desconectar()));
  protected readonly borde = computed(() =>
    this.salud() === 'error' ? 'border-red-200' : this.salud() === 'requiere_accion' ? 'border-amber-300' : 'border-line',
  );

  /** Conectado se confirma (corta la automatización); cancelar un ingreso en curso, no. */
  protected pedirDesconexion(): void {
    if (this.salud() === 'conectado') this.confirmando.set(true);
    else this.desconectado.emit();
  }
}
