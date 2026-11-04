import { CurrencyPipe, DatePipe } from '@angular/common';
import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { EstadoRevision, OrigenObservacion, Revision } from '../../core/models/revision.models';
import { RevisionesStore } from '../../core/services/revisiones.store';

type FiltroEstado = EstadoRevision | 'todos';

export const ESTADOS: Record<EstadoRevision, { titulo: string; corto: string; clase: string; punto: string }> = {
  pendiente: { titulo: 'Falta revisión', corto: 'Falta revisión', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500' },
  en_revision: { titulo: 'En revisión', corto: 'En revisión', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600' },
  observado: { titulo: 'Con observaciones', corto: 'Observado', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  aprobado: { titulo: 'Revisado y aprobado', corto: 'Aprobado', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
};

const ORIGENES: Record<OrigenObservacion, { titulo: string; clase: string }> = {
  nuestro: { titulo: 'Error nuestro / del equipo', clase: 'bg-red-50 text-red-700' },
  cliente: { titulo: 'Error del cliente', clase: 'bg-amber-50 text-amber-800' },
  arca: { titulo: 'Dato de ARCA', clase: 'bg-brand-50 text-brand-700' },
};

const ACCIONES: Record<string, string> = {
  enviado: 'Envió a revisión',
  pendiente: 'Volvió a «falta revisión»',
  en_revision: 'Empezó la revisión',
  observado: 'Devolvió con observaciones',
  aprobado: 'Aprobó',
  control: 'Tildó',
  control_quitado: 'Destildó',
  observacion: 'Observó',
  resuelta: 'Resolvió',
  reabierta: 'Reabrió la observación',
};

@Component({
  selector: 'app-revision',
  imports: [CurrencyPipe, DatePipe],
  templateUrl: './revision.html',
})
export class RevisionPantalla {
  protected readonly store = inject(RevisionesStore);
  protected readonly estados = ESTADOS;
  protected readonly origenes = ORIGENES;
  protected readonly ordenEstados: EstadoRevision[] = ['pendiente', 'en_revision', 'observado', 'aprobado'];
  protected readonly ordenOrigenes: OrigenObservacion[] = ['nuestro', 'cliente', 'arca'];

  protected readonly filtro = signal<FiltroEstado>('todos');
  protected readonly busqueda = signal('');
  protected readonly seleccionId = signal<string | null>(null);

  /** Formularios del panel: se vacían al cambiar de revisión. */
  protected readonly textoObservacion = linkedSignal({ source: this.seleccionId, computation: () => '' });
  protected readonly origenObservacion = linkedSignal<string | null, OrigenObservacion>({ source: this.seleccionId, computation: () => 'nuestro' });
  protected readonly comentario = linkedSignal({ source: this.seleccionId, computation: () => '' });
  protected readonly confirmarBorrar = linkedSignal({ source: this.seleccionId, computation: () => false });

  protected readonly cantidades = computed(() => {
    const cuenta: Record<FiltroEstado, number> = { todos: 0, pendiente: 0, en_revision: 0, observado: 0, aprobado: 0 };
    for (const r of this.store.revisiones()) {
      cuenta[r.estado]++;
      cuenta.todos++;
    }
    return cuenta;
  });

  protected readonly visibles = computed(() => {
    const texto = this.busqueda().trim().toLowerCase();
    return this.store.revisiones().filter(
      (r) =>
        (this.filtro() === 'todos' || r.estado === this.filtro()) &&
        (!texto || `${r.empresa} ${r.periodo} ${r.enviado_por} ${r.revisor ?? ''}`.toLowerCase().includes(texto)),
    );
  });

  protected readonly seleccionada = computed(() => {
    const id = this.seleccionId() ?? this.visibles()[0]?.id;
    return this.store.revisiones().find((r) => r.id === id) ?? null;
  });

  protected readonly listaControles = computed(() => Object.entries(this.store.controles()));

  constructor() {
    this.store.cargar();
  }

  protected iniciales(nombre: string): string {
    const partes = nombre.trim().split(/\s+/).filter(Boolean);
    return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
  }

  protected tildados(r: Revision): number {
    return Object.values(r.controles).filter(Boolean).length;
  }

  protected abiertas(r: Revision): number {
    return r.observaciones.filter((o) => !o.resuelta).length;
  }

  /** Qué falta para poder aprobar (vacío = se puede). */
  protected faltaParaAprobar(r: Revision): string | null {
    const controles = Object.keys(r.controles).length - this.tildados(r);
    if (controles) return `Faltan ${controles} control${controles === 1 ? '' : 'es'} por tildar.`;
    const abiertas = this.abiertas(r);
    if (abiertas) return `Hay ${abiertas} observación${abiertas === 1 ? '' : 'es'} sin resolver.`;
    return null;
  }

  protected accion(evento: string): string {
    return ACCIONES[evento] ?? evento;
  }

  protected alternarControl(r: Revision, control: string): void {
    this.store.marcarControl(r.id, control, !r.controles[control]).subscribe();
  }

  protected agregarObservacion(r: Revision): void {
    const texto = this.textoObservacion().trim();
    if (!texto) return;
    this.store.observar(r.id, texto, this.origenObservacion()).subscribe((ok) => ok && this.textoObservacion.set(''));
  }

  protected cambiarEstado(r: Revision, estado: EstadoRevision): void {
    this.store.cambiarEstado(r.id, estado, this.comentario()).subscribe((ok) => ok && this.comentario.set(''));
  }

  protected eliminar(r: Revision): void {
    this.store.eliminar(r.id);
    this.seleccionId.set(null);
  }
}
