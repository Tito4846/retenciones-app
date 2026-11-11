import { DatePipe } from '@angular/common';
import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { LotesStore } from '../../core/services/lotes.store';
import { SesionService } from '../../core/services/sesion.service';
import { AccionesLote } from './acciones-lote';
import { BandejaLotes } from './bandeja-lotes';
import { GestorLote } from './gestor-lote';
import { HistorialLote } from './historial-lote';
import { MarcadorCruce } from './marcador-cruce';
import { SeguimientoLote } from './seguimiento-lote';
import { centavos, CONCILIADO, ESTADOS, periodo, Permisos, pesos } from './revision.util';
import { VersionesLote } from './versiones-lote';

/**
 * Verificación y aprobación: bandeja a la izquierda, lote a la derecha. El trabajo
 * se hace en el Excel: acá se baja, se sube y se adjuntan los comprobantes.
 */
@Component({
  selector: 'app-revision',
  imports: [AccionesLote, BandejaLotes, DatePipe, GestorLote, HistorialLote, MarcadorCruce, RouterLink, SeguimientoLote, VersionesLote],
  templateUrl: './revision.html',
})
export class RevisionPantalla {
  protected readonly store = inject(LotesStore);
  protected readonly sesion = inject(SesionService);
  private readonly router = inject(Router);
  protected readonly estados = ESTADOS;
  protected readonly periodo = periodo;

  protected readonly d = this.store.detalle;
  private readonly ruta = inject(ActivatedRoute);
  /** Al llegar recién generado (o desde un aviso), ya viene elegido. */
  protected readonly seleccionId = signal<string | null>(this.ruta.snapshot.queryParamMap.get('lote'));

  constructor() {
    // Un aviso del menú con la pantalla ya abierta cambia solo el ?lote=: hay que seguirlo.
    this.ruta.queryParamMap.pipe(takeUntilDestroyed()).subscribe((q) => {
      const lote = q.get('lote');
      if (lote) this.seleccionId.set(lote);
    });
  }
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
      eliminar: !abierto && !!lote && supervisor && !!this.store.catalogo()?.eliminar_cerrados,
    };
  });

  protected readonly totales = computed(() => {
    const t = this.d()?.totales;
    return { sistema: centavos(t?.sistema), declarado: centavos(t?.declarado), neta: centavos(t?.neta) };
  });

  private readonly pendientes = computed(() => (this.d()?.partidas ?? []).filter((p) => !p.excluida && p.estado !== CONCILIADO && p.faltas.length).length);

  protected readonly ultimoRechazo = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'rechazo') ?? null);

  protected readonly trabasEnviar = computed(() => (this.d()?.compuertas.enviar ?? []).map((e) => e.mensaje));
  protected readonly trabasAprobar = computed(() => (this.d()?.compuertas.aprobar ?? []).map((e) => e.mensaje));

  /** Sin el lote borrado en la dirección: al recargar intentaría abrirlo. */
  protected alEliminar(): void {
    this.seleccionId.set(null);
    this.router.navigate([], { queryParams: {} });
  }

  /** Lo que quedó sin resolver, en una línea: no frena, lo evalúa quien aprueba. Los comprobantes elegidos ya cuentan. */
  protected readonly pendiente = computed(() => {
    const lista: string[] = [];
    if (this.totales().neta) lista.push(`diferencia neta de ${pesos(this.totales().neta / 100)}`);
    const n = this.pendientes();
    if (n) lista.push(`${n} ${n === 1 ? 'excepción' : 'excepciones'} sin completar`);
    if (this.d()?.pendientes.some((e) => e.codigo === 'SIN_RESPALDO') && !this.evidencia().length) lista.push('sin comprobantes');
    return lista;
  });
}
