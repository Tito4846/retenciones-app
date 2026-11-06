import { Component, computed, inject, input, signal } from '@angular/core';
import { CuitPipe } from '../../core/cuit.pipe';
import { Partida } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { centavos, pesos } from './revision.util';

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

type Filtro = 'todas' | 'sin_completar';
type Situacion = 'sin_completar' | 'justificada' | 'quitada';

const SITUACION: Record<Situacion, { texto: string; clase: string }> = {
  sin_completar: { texto: 'Sin completar', clase: 'text-amber-800' },
  justificada: { texto: 'Justificada', clase: 'text-emerald-700' },
  quitada: { texto: 'Quitada del cruce', clase: 'text-muted' },
};

/** Las excepciones del lote con lo que escribió quien las trabajó: se leen acá y se editan en el Excel. */
@Component({
  selector: 'app-excepciones-lote',
  imports: [CuitPipe],
  host: { class: 'mt-2 block' },
  template: `
    @if (cuenta().sin_completar && cuenta().sin_completar < partidas().length) {
      <div class="mb-2 flex gap-1 text-xs font-semibold" role="group" aria-label="Filtrar excepciones">
        <button type="button" (click)="filtro.set('todas')" [attr.aria-pressed]="filtro() === 'todas'" class="rounded-full border px-3 py-1"
          [class]="filtro() === 'todas' ? 'border-brand-700 bg-brand-700 text-white' : 'border-line bg-white hover:border-brand-300'">Todas ({{ partidas().length }})</button>
        <button type="button" (click)="filtro.set('sin_completar')" [attr.aria-pressed]="filtro() === 'sin_completar'" class="rounded-full border px-3 py-1"
          [class]="filtro() === 'sin_completar' ? 'border-brand-700 bg-brand-700 text-white' : 'border-line bg-white hover:border-brand-300'">Sin completar ({{ cuenta().sin_completar }})</button>
      </div>
    }
    <!-- Anchos fijos: la tabla entra en el panel y el agente se queda con lo que sobra -->
    <table class="w-full table-fixed border-separate border-spacing-0 border-y border-line bg-white text-left text-[13px]" aria-label="Excepciones del lote">
      <colgroup>
        <col />
        <col class="w-21" />
        <col class="w-23" />
        <col class="w-23" />
        <col class="w-25" />
        <col class="w-23" />
      </colgroup>
      <!-- -top-3: el p-3 del panel, para que no asomen filas sobre la cabecera -->
      <thead class="sticky -top-3 z-10 text-muted [&_th]:bg-canvas">
        <tr>
          <th scope="col" class="px-2 py-2 font-semibold">Agente / Proveedor</th>
          <th scope="col" class="px-2 py-2 font-semibold">Comprobante</th>
          <th scope="col" class="px-2 py-2 text-right font-semibold" title="Importe registrado en el Libro Mayor">Monto Sistema</th>
          <th scope="col" class="px-2 py-2 text-right font-semibold">Monto ARCA</th>
          <th scope="col" class="px-2 py-2 text-right font-semibold">Diferencia</th>
          <th scope="col" class="px-2 py-2 font-semibold">Motivo detectado</th>
        </tr>
      </thead>
      @for (f of filas(); track f.clave) {
        <tbody class="[&_tr:first-child_td]:border-t [&_td]:border-line" [class.opacity-60]="f.situacion === 'quitada'">
          <tr class="align-top">
            <td class="px-2 pt-2 pb-1">
              <p class="font-semibold wrap-break-word">{{ f.razon_social || 'Sin razón social' }}</p>
              @if (f.cuit) { <p class="truncate text-muted tabular-nums">{{ f.cuit | cuit }}</p> }
            </td>
            <td class="px-2 pt-2 pb-1 wrap-anywhere">{{ f.comprobante || '—' }}</td>
            <td class="px-2 pt-2 pb-1 text-right whitespace-nowrap tabular-nums">
              {{ f.mayor }}
              @if (f.ajustado) { <p class="text-xs text-muted" title="Lo que decía el Mayor antes del ajuste">{{ f.ajustado }}</p> }
            </td>
            <td class="px-2 pt-2 pb-1 text-right whitespace-nowrap tabular-nums">{{ f.arca }}</td>
            <td class="px-2 pt-2 pb-1 text-right font-semibold whitespace-nowrap tabular-nums"
              [class.text-red-700]="f.diferencia < 0" [class.text-emerald-700]="f.diferencia === 0">{{ f.diferenciaTexto }}</td>
            <td class="px-2 pt-2 pb-1">
              <div class="flex flex-wrap gap-1">
                @for (m of f.motivos; track m.texto) {
                  <span class="rounded px-1.5 py-0.5 font-medium ring-1 ring-inset" [class]="m.clase" [title]="m.ayuda">{{ m.texto }}</span>
                }
              </div>
            </td>
          </tr>
          <tr>
            <td colspan="6" class="px-2 pb-2 text-xs">
              <p>
                <span class="font-semibold" [class]="situaciones[f.situacion].clase">{{ situaciones[f.situacion].texto }}</span>
                @if (f.justificacion) { <span class="font-semibold"> · {{ f.justificacion }}</span> }
                @if (f.comentario) { <span> · {{ f.comentario }}</span> }
                @if (f.editadoPor) { <span class="text-muted"> — {{ f.editadoPor }}</span> }
              </p>
            </td>
          </tr>
        </tbody>
      }
    </table>
    @if (editar() && cuenta().sin_completar) {
      <p class="mt-2 text-sm text-muted">Editá estas partidas en la hoja 'CONCILIACION' del Excel y volvé a subirlo.</p>
    }
  `,
})
export class ExcepcionesLote {
  private readonly store = inject(LotesStore);
  protected readonly situaciones = SITUACION;

  readonly partidas = input.required<Partida[]>();
  readonly editar = input(false);

  protected readonly filtro = signal<Filtro>('todas');

  private situacion(p: Partida): Situacion {
    if (p.excluida) return 'quitada';
    return p.faltas.length ? 'sin_completar' : 'justificada';
  }

  protected readonly cuenta = computed(() => ({ sin_completar: this.partidas().filter((p) => this.situacion(p) === 'sin_completar').length }));

  protected readonly filas = computed(() => {
    const catalogo = this.store.catalogo();
    const sinCompletar = this.filtro() === 'sin_completar' && this.cuenta().sin_completar > 0;
    const orden: Situacion[] = ['sin_completar', 'justificada', 'quitada'];
    return this.partidas()
      .filter((p) => !sinCompletar || this.situacion(p) === 'sin_completar')
      .sort((a, b) => orden.indexOf(this.situacion(a)) - orden.indexOf(this.situacion(b)))
      .map((p) => {
        // Si ya se ajustó en el Excel, lo que cuenta es el importe ajustado.
        const mayor = p.importe_ajustado ?? p.importe_erp;
        const diferencia = centavos(p.importe_fisco) - centavos(mayor);
        const motivo = MOTIVOS[p.codigo] ?? { texto: p.estado, clase: OBSERVADA };
        const motivos = [{ ...motivo, ayuda: catalogo?.codigos[p.codigo] ?? p.estado }];
        if (p.flags.includes('FUERA_PER')) {
          motivos.push({ texto: 'Fuera de período', clase: OBSERVADA, ayuda: catalogo?.flags['FUERA_PER'] ?? '' });
        }
        return {
          clave: p.clave,
          razon_social: p.razon_social,
          cuit: p.cuit,
          comprobante: p.certificado || p.documento_erp,
          mayor: pesos(mayor),
          ajustado: p.importe_ajustado === null ? '' : p.importe_erp === null ? 'ajustado' : `antes ${pesos(p.importe_erp)}`,
          arca: pesos(p.importe_fisco),
          diferencia,
          diferenciaTexto: pesos(diferencia / 100),
          motivos,
          situacion: this.situacion(p),
          justificacion: catalogo?.justificaciones[p.justificacion] ?? p.justificacion,
          comentario: p.comentario,
          editadoPor: p.editado_por,
        };
      });
  });
}
