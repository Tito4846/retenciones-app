import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, signal } from '@angular/core';
import { DetalleLote, OrigenObservacion } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { ORDEN_ORIGENES, ORIGENES } from './revision.util';

/** Lo que mira el supervisor sobre todo el lote: controles, observaciones generales y respaldos generales. */
@Component({
  selector: 'app-controles-lote',
  imports: [DatePipe],
  host: { class: 'block space-y-5' },
  template: `
    @let det = detalle();
    <section>
      <h3 class="font-semibold">Controles <span class="font-normal text-muted">· {{ hechos() }} de {{ lista().length }}</span></h3>
      <ul class="mt-2 space-y-1.5">
        @for (c of lista(); track c.clave) {
          <li class="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm" [class]="c.ok ? 'border-emerald-200 bg-emerald-50/50' : 'border-line bg-white'">
            <input type="checkbox" class="mt-0.5 h-4 w-4 shrink-0 accent-emerald-600" [checked]="c.ok" [disabled]="!revisar() || store.ocupado()"
              (change)="store.marcarControl(det.lote.id, c.clave, det.lote.version_vigente, !c.ok).subscribe()" [attr.aria-label]="c.texto" />
            <span class="flex-1">{{ c.texto }}</span>
            @if (c.usuario) { <span class="shrink-0 text-xs text-muted">{{ c.usuario }}</span> }
          </li>
        }
      </ul>
    </section>

    <section>
      <h3 class="font-semibold">Observaciones sobre todo el lote</h3>
      <ul class="mt-2 space-y-1.5">
        @for (o of generales(); track o.id) {
          <li class="flex items-start gap-2 rounded-lg px-3 py-2 text-sm" [class]="o.resuelta_utc ? 'bg-canvas text-muted' : 'bg-red-50 text-red-900'">
            <span class="flex-1" [class.line-through]="o.resuelta_utc"><strong>{{ o.autor_nombre }}</strong> ({{ origenes[o.origen] }}): {{ o.texto }}</span>
            @if (revisar()) {
              <button type="button" (click)="store.resolver(det.lote.id, o.id, !o.resuelta_utc).subscribe()" class="shrink-0 text-xs font-semibold hover:underline">{{ o.resuelta_utc ? 'Reabrir' : 'Resuelta' }}</button>
            }
          </li>
        } @empty {
          <li class="text-sm text-muted">No hay.</li>
        }
      </ul>
      @if (revisar()) {
        <div class="mt-2 space-y-2 rounded-lg border border-line bg-white p-2">
          <textarea rows="2" [value]="texto()" (input)="texto.set($any($event.target).value)" aria-label="Observación general"
            class="w-full rounded border border-line px-2 py-1 text-sm outline-none focus:border-brand-400" placeholder="Qué hay que corregir en todo el lote"></textarea>
          <div class="flex gap-2">
            <select [value]="origen()" (change)="origen.set($any($event.target).value)" aria-label="Origen" class="rounded border border-line bg-white px-2 py-1 text-sm">
              @for (o of ordenOrigenes; track o) { <option [value]="o">{{ origenes[o] }}</option> }
            </select>
            <span class="flex-1"></span>
            <button type="button" (click)="observar()" [disabled]="!texto().trim() || store.ocupado()" class="rounded bg-red-600 px-3 py-1 text-sm font-semibold text-white disabled:opacity-50">Guardar</button>
          </div>
        </div>
      }
    </section>

    <section>
      <div class="flex items-center gap-2">
        <h3 class="mr-auto font-semibold">Respaldos generales</h3>
        @if (editar()) {
          <label class="cursor-pointer rounded-lg border border-line bg-white px-3 py-1.5 text-sm font-semibold hover:bg-canvas">
            + Respaldo general
            <input type="file" class="sr-only" [attr.accept]="(store.catalogo()?.adjuntos_permitidos ?? []).join(',')" (change)="adjuntar($event)" />
          </label>
        }
      </div>
      <ul class="mt-2 divide-y divide-line rounded-lg border border-line bg-white text-sm">
        @for (a of adjuntos(); track a.id) {
          <li class="flex flex-wrap items-center gap-2 px-3 py-2">
            <button type="button" (click)="store.bajarAdjunto(det.lote.id, a.id, a.nombre)" class="font-semibold text-brand-700 hover:underline">📎 {{ a.nombre }}</button>
            <span class="text-xs text-muted">{{ a.autor_nombre }} · {{ a.creado_utc | date: 'dd/MM HH:mm' }}</span>
            @if (editar()) {
              <button type="button" (click)="store.retirarAdjunto(det.lote.id, a.id).subscribe()" class="ml-auto text-xs font-semibold text-muted hover:text-red-700">Quitar</button>
            }
          </li>
        } @empty {
          <li class="px-3 py-2 text-muted">Todavía no hay.</li>
        }
      </ul>
    </section>
  `,
})
export class ControlesLote {
  protected readonly store = inject(LotesStore);
  protected readonly origenes = ORIGENES;
  protected readonly ordenOrigenes = ORDEN_ORIGENES;

  readonly detalle = input.required<DetalleLote>();
  readonly revisar = input(false);
  readonly editar = input(false);

  protected readonly texto = signal('');
  protected readonly origen = signal<OrigenObservacion>('nuestro');

  protected readonly lista = computed(() =>
    Object.entries(this.store.catalogo()?.controles ?? {}).map(([clave, texto]) => {
      const marca = this.detalle().controles[clave];
      return { clave, texto, ok: marca?.ok ?? false, usuario: marca?.ok ? marca.usuario : '' };
    }),
  );
  protected readonly hechos = computed(() => this.lista().filter((c) => c.ok).length);
  protected readonly generales = computed(() => this.detalle().observaciones.filter((o) => !o.clave_partida));
  protected readonly adjuntos = computed(() => this.detalle().adjuntos.filter((a) => !a.clave_partida));

  protected observar(): void {
    const { id, version_vigente } = this.detalle().lote;
    this.store.observar(id, version_vigente, this.texto().trim(), this.origen(), null).subscribe((ok) => ok && this.texto.set(''));
  }

  protected adjuntar(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    const archivo = entrada.files?.[0];
    entrada.value = '';
    if (archivo) this.store.subirAdjunto(this.detalle().lote.id, archivo, null).subscribe();
  }
}
