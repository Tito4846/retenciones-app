import { DatePipe } from '@angular/common';
import { Component, effect, inject, input, signal } from '@angular/core';
import { EventoAuditoria } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';

const ACCIONES: Record<string, string> = {
  lote_creado: 'Creó el lote',
  lote_rectificativo: 'Creó el lote rectificativo',
  version_generada: 'Generó una versión',
  version_subida: 'Subió una versión',
  carga_rechazada: 'Subida rechazada por la validación',
  partida_actualizada: 'Corrigió una partida',
  adjunto_subido: 'Adjuntó un respaldo',
  adjunto_retirado: 'Retiró un respaldo',
  enviado: 'Envió a revisión',
  tomado: 'Tomó el lote para revisar',
  reasignado: 'Se reasignó la revisión',
  control: 'Marcó un control',
  observacion: 'Observó',
  observacion_resuelta: 'Dio por resuelta una observación',
  observacion_reabierta: 'Reabrió una observación',
  rechazado: 'Devolvió para corregir',
  aprobado: 'Aprobó',
  anulado: 'Anuló',
  exportacion: 'Generó la exportación fiscal',
  descarga: 'Descargó',
  acceso_denegado: 'Acceso denegado',
};

/** La auditoría del lote. Se pide de nuevo cada vez que cambia la versión o el lote. */
@Component({
  selector: 'app-historial-lote',
  imports: [DatePipe],
  host: { class: 'block' },
  template: `
    @if (eventos(); as lista) {
      <ol class="relative space-y-3 border-l border-line pl-5">
        @for (e of lista; track e.id) {
          <li class="relative text-sm">
            <span class="absolute top-1.5 -left-[1.6rem] h-2.5 w-2.5 rounded-full ring-4 ring-canvas"
              [class]="e.action_type === 'acceso_denegado' || e.action_type === 'carga_rechazada' || e.action_type === 'rechazado' ? 'bg-red-500' : e.action_type === 'aprobado' ? 'bg-emerald-500' : 'bg-brand-600'"></span>
            <p><strong>{{ e.user_nombre }}</strong> · {{ accion(e.action_type) }}</p>
            @if (detalle(e); as texto) { <p class="text-xs whitespace-pre-line text-muted">{{ texto }}</p> }
            <p class="text-xs text-muted tabular-nums" [title]="'UTC ' + e.ts_utc + ' · IP ' + (e.ip_address || '—')">{{ e.ts_utc | date: 'dd/MM/yyyy HH:mm:ss' }}</p>
          </li>
        }
      </ol>
      <p class="mt-4 text-xs text-muted">Registro que no se puede editar ni borrar. Pasá el mouse por la hora para ver la IP y la hora UTC.</p>
    } @else {
      <p class="py-6 text-center text-sm text-muted">Cargando…</p>
    }
  `,
})
export class HistorialLote {
  private readonly store = inject(LotesStore);
  readonly loteId = input.required<string>();
  readonly version = input.required<number>();
  protected readonly eventos = signal<EventoAuditoria[] | null>(null);

  constructor() {
    effect(() => {
      this.version();
      this.store.auditoria(this.loteId()).subscribe((eventos) => this.eventos.set(eventos));
    });
  }

  protected accion(evento: string): string {
    return ACCIONES[evento] ?? evento.replace('migrado:', 'Bandeja anterior: ');
  }

  protected detalle(e: EventoAuditoria): string {
    const d = e.detalle;
    const partes: string[] = [];
    for (const clave of ['numero', 'motivo', 'comentario', 'texto', 'control', 'nombre', 'archivo', 'tipo', 'intento', 'checker_anterior']) {
      const valor = d[clave];
      if (valor !== undefined && valor !== null && valor !== '') {
        partes.push(clave === 'numero' ? `v${valor}` : clave === 'control' ? (this.store.catalogo()?.controles[String(valor)] ?? String(valor)) : String(valor));
      }
    }
    if (d['ok'] === false) partes.push('(destildado)');
    if (d['despues']) partes.push(JSON.stringify(d['despues']));
    if (Array.isArray(d['errores'])) partes.push(`${d['errores'].length} error(es)`);
    return partes.join(' · ');
  }
}
