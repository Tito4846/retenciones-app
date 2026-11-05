import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, linkedSignal, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DetalleLote, Diff, Version } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { pesos } from './revision.util';

const ORIGEN_VERSION: Record<string, string> = {
  generado: 'Generada del cruce',
  subido: 'Excel subido',
  edicion: 'Corrección en pantalla',
  migrado: 'Bandeja anterior',
};

const CAMPOS: Record<string, string> = {
  estado: 'Estado', codigo: 'Motivo', importe_fisco: 'Importe ARCA', importe_erp: 'Importe Mayor', diferencia: 'Diferencia',
  justificacion: 'Justificación', comentario: 'Comentario', importe_ajustado: 'Importe ajustado', excluida: 'Quitada',
};

/** Las versiones del lote, el trabajo en Excel y qué cambió entre una versión y otra. */
@Component({
  selector: 'app-versiones-lote',
  imports: [DatePipe, RouterLink],
  host: { class: 'block' },
  template: `
    @let det = detalle();
    @if (editar()) {
      <div class="mb-4 rounded-lg bg-canvas/60 p-4 text-sm">
        @if (!det.partidas.length) {
          <p class="font-semibold">Este lote viene de la bandeja anterior y no tiene partidas.</p>
          <p class="mt-1 text-muted">
            Abrí su conciliación en <a routerLink="/conciliacion" class="font-semibold text-brand-700 underline">Conciliación</a>,
            tocá «Crear lote para revisión» y elegí «Versión nueva de {{ det.lote.empresa }}».
          </p>
        } @else {
          <p class="font-semibold">¿Preferís trabajar en Excel?</p>
          <p class="mt-1 text-muted">
            Bajá el Excel, completá Justificación y Comentario en la hoja CONCILIACION y subilo. Se valida antes de quedar como versión nueva:
            si alguien tocó importes, fechas u otras columnas bloqueadas, se rechaza y te dice qué fila.
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            @if (vigente(); as v) {
              <button type="button" (click)="store.bajarVersion(det.lote.id, v.numero, v.nombre_archivo)"
                class="rounded-lg border border-line bg-white px-3 py-2 font-semibold hover:bg-canvas">Bajar Excel (versión {{ v.numero }})</button>
            }
            <label class="cursor-pointer rounded-lg bg-brand-700 px-3 py-2 font-semibold text-white hover:bg-brand-800">
              Subir Excel trabajado
              <input type="file" accept=".xlsx" class="sr-only" (change)="subir($event)" [disabled]="store.ocupado()" />
            </label>
          </div>
        }
      </div>
    }
    <ul class="space-y-2">
      @for (v of det.versiones; track v.id) {
        <li class="rounded-lg border border-line bg-white px-4 py-3 text-sm">
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded bg-brand-700 px-2 py-0.5 text-xs font-bold text-white">Versión {{ v.numero }}</span>
            <span class="font-semibold">{{ origen(v) }}</span>
            <span class="text-muted">· {{ v.autor_nombre }} · {{ v.creado_utc | date: 'dd/MM/yyyy HH:mm' }}</span>
            @if (v.sellada_utc) { <span class="rounded bg-zinc-100 px-1.5 text-xs text-zinc-600">sellada</span> }
            <span class="flex-1"></span>
            <button type="button" (click)="store.bajarVersion(det.lote.id, v.numero, v.nombre_archivo)" class="text-xs font-semibold text-brand-700 hover:underline">Bajar Excel</button>
          </div>
          @if (v.validacion.advertencias?.length || v.validacion.errores?.length) {
            <ul class="mt-2 list-disc pl-5 text-xs">
              @for (e of v.validacion.errores ?? []; track $index) { <li class="text-red-700">{{ e.mensaje }}</li> }
              @for (e of v.validacion.advertencias ?? []; track $index) {
                @if ($index < 5) { <li class="text-amber-800">@if (e.fila) { Fila {{ e.fila }}: }{{ e.mensaje }}</li> }
              }
              @if ((v.validacion.advertencias?.length ?? 0) > 5) { <li class="text-muted">y {{ v.validacion.advertencias!.length - 5 }} aviso(s) más</li> }
            </ul>
          }
        </li>
      }
    </ul>
    @if (det.versiones.length > 1) {
      <div class="mt-5 rounded-lg border border-line bg-white p-4">
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <span class="font-semibold">Qué cambió de la versión</span>
          <select [value]="desde()" (change)="desde.set(+$any($event.target).value)" aria-label="Desde" class="rounded border border-line px-2 py-1">
            @for (v of det.versiones; track v.id) { <option [value]="v.numero">{{ v.numero }}</option> }
          </select>
          <span>a la</span>
          <select [value]="hasta()" (change)="hasta.set(+$any($event.target).value)" aria-label="Hasta" class="rounded border border-line px-2 py-1">
            @for (v of det.versiones; track v.id) { <option [value]="v.numero">{{ v.numero }}</option> }
          </select>
          <button type="button" (click)="comparar()" class="rounded-lg bg-brand-700 px-3 py-1 font-semibold text-white">Ver cambios</button>
        </div>
        @if (diff(); as df) {
          <div class="mt-3 text-sm">
            <p class="text-muted">{{ df.cambiadas.length }} cambiada(s), {{ df.agregadas.length }} agregada(s), {{ df.quitadas.length }} quitada(s).</p>
            <ul class="mt-2 space-y-1">
              @for (c of df.cambiadas; track c.clave) {
                <li>
                  <span class="font-semibold">{{ c.razon_social || c.clave }}</span>:
                  @for (x of c.cambios; track x.campo; let ultimo = $last) {
                    {{ titulo(x.campo) }} <span class="text-red-700 line-through">{{ valor(x.campo, x.antes) }}</span>
                    → <span class="text-emerald-700">{{ valor(x.campo, x.despues) }}</span>@if (!ultimo) {; }
                  }
                </li>
              }
              @for (x of df.agregadas; track x.clave) { <li class="text-emerald-700">+ {{ x.razon_social }} {{ pesos(x.importe_fisco ?? x.importe_erp) }} ({{ x.estado }})</li> }
              @for (x of df.quitadas; track x.clave) { <li class="text-red-700">− {{ x.razon_social }} {{ pesos(x.importe_fisco ?? x.importe_erp) }} ({{ x.estado }})</li> }
            </ul>
          </div>
        }
      </div>
    }
  `,
})
export class VersionesLote {
  protected readonly store = inject(LotesStore);
  protected readonly pesos = pesos;

  readonly detalle = input.required<DetalleLote>();
  readonly editar = input(false);

  protected readonly vigente = computed(() => this.detalle().versiones.find((v) => v.numero === this.detalle().lote.version_vigente) ?? null);
  protected readonly desde = signal(1);
  protected readonly hasta = signal(1);
  private readonly loteId = computed(() => this.detalle().lote.id);
  protected readonly diff = linkedSignal<string, Diff | null>({ source: this.loteId, computation: () => null });

  constructor() {
    effect(() => {
      const n = this.detalle().lote.version_vigente;
      this.hasta.set(n);
      this.desde.set(Math.max(1, n - 1));
    });
  }

  protected origen(v: Version): string {
    return v.correccion_checker ? 'Corrección del supervisor' : (ORIGEN_VERSION[v.origen] ?? v.origen);
  }

  protected subir(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    const archivo = entrada.files?.[0];
    entrada.value = '';
    const { id, version_vigente } = this.detalle().lote;
    if (archivo) this.store.subirVersion(id, archivo, version_vigente).subscribe();
  }

  protected comparar(): void {
    this.store.diff(this.loteId(), this.desde(), this.hasta()).subscribe((d) => this.diff.set(d));
  }

  protected titulo(campo: string): string {
    return CAMPOS[campo] ?? campo;
  }

  protected valor(campo: string, valor: string | null): string {
    if (campo === 'justificacion') return (valor && this.store.catalogo()?.justificaciones[valor]) || valor || '—';
    if (campo === 'excluida') return Number(valor) ? 'Sí' : 'No';
    return valor || '—';
  }
}
