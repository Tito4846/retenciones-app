import { DatePipe } from '@angular/common';
import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ErrorValidacion } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { SesionService } from '../../core/services/sesion.service';
import { AccionesLote } from './acciones-lote';
import { BandejaLotes } from './bandeja-lotes';
import { GestorLote } from './gestor-lote';
import { HistorialLote } from './historial-lote';
import { MarcadorCruce } from './marcador-cruce';
import { centavos, CONCILIADO, ESTADOS, periodo, Permisos, pesos } from './revision.util';
import { VersionesLote } from './versiones-lote';

/** Las trabas que la pantalla resume sola: del resto se ocupa lo que diga el backend. */
const TRABAS_LOCALES = new Set(['DIFERENCIA_NETA', 'SIN_JUSTIFICACION', 'SIN_COMENTARIO']);

/**
 * Verificación y aprobación: bandeja a la izquierda, lote a la derecha. El trabajo
 * se hace en el Excel: acá se baja, se sube y se adjuntan los comprobantes.
 */
@Component({
  selector: 'app-revision',
  imports: [AccionesLote, BandejaLotes, DatePipe, GestorLote, HistorialLote, MarcadorCruce, RouterLink, VersionesLote],
  templateUrl: './revision.html',
})
export class RevisionPantalla {
  protected readonly store = inject(LotesStore);
  protected readonly sesion = inject(SesionService);
  protected readonly estados = ESTADOS;
  protected readonly periodo = periodo;

  protected readonly d = this.store.detalle;
  /** Al llegar recién generado, ya viene elegido. */
  protected readonly seleccionId = signal<string | null>(inject(ActivatedRoute).snapshot.queryParamMap.get('lote'));
  private readonly loteId = computed(() => this.d()?.lote.id ?? null);

  /** Comprobantes a la espera de confirmar. */
  protected readonly evidencia = linkedSignal<string | null, File[]>({ source: this.loteId, computation: () => [] });

  protected readonly permisos = computed<Permisos>(() => {
    const lote = this.d()?.lote;
    const yo = this.sesion.usuario()?.id ?? '';
    const supervisor = this.sesion.esSupervisor();
    const prepara = !!lote && (lote.estado === 'borrador' || lote.estado === 'rechazado') && this.sesion.puedePreparar();
    const revisar = lote?.estado === 'en_revision' && supervisor && lote.checker_id === yo;
    const abierto = !!lote && lote.estado !== 'aprobado' && lote.estado !== 'anulado';
    const autor = !!lote && lote.autores.includes(yo);
    return {
      editar: prepara || revisar,
      tomar: lote?.estado === 'pendiente' && supervisor && !autor,
      reasignar: lote?.estado === 'en_revision' && supervisor && lote.checker_id !== yo && !autor,
      revisar,
      autor: abierto && supervisor && autor,
      anular: !!lote && ['borrador', 'pendiente', 'rechazado'].includes(lote.estado) && this.sesion.puedePreparar(),
      exportar: lote?.estado === 'aprobado',
    };
  });

  protected readonly totales = computed(() => {
    const t = this.d()?.totales;
    return { sistema: centavos(t?.sistema), declarado: centavos(t?.declarado), neta: centavos(t?.neta) };
  });

  private readonly pendientes = computed(() => (this.d()?.partidas ?? []).filter((p) => !p.excluida && p.estado !== CONCILIADO && p.faltas.length).length);

  protected readonly ultimoRechazo = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'rechazo') ?? null);
  protected readonly ultimoEnvio = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'envio') ?? null);

  protected readonly trabasEnviar = computed(() => this.trabas(this.d()?.compuertas.enviar ?? []));
  protected readonly trabasAprobar = computed(() => this.trabas(this.d()?.compuertas.aprobar ?? []));

  /** Lo que bloquea el botón principal, en una línea. Los comprobantes elegidos ya cuentan: viajan con la confirmación. */
  private trabas(compuertas: ErrorValidacion[]): string[] {
    const trabas: string[] = [];
    if (this.totales().neta) trabas.push(`la diferencia neta es ${pesos(this.totales().neta / 100)}`);
    const n = this.pendientes();
    if (n) trabas.push(`faltan completar ${n} ${n === 1 ? 'excepción' : 'excepciones'} en el Excel`);
    const resto = compuertas.filter((e) => !TRABAS_LOCALES.has(e.codigo) && !(e.codigo === 'SIN_RESPALDO' && this.evidencia().length));
    return [...trabas, ...resto.map((e) => e.mensaje)];
  }
}
