import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
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
import { contarSinCompletar, motivosSinAprobar } from './revision.reglas';
import { centavos, ESTADOS, periodo, Permisos, pesos } from './revision.util';
import { VersionesLote } from './versiones-lote';

/**
 * Verificación y aprobación: bandeja a la izquierda, lote a la derecha. Cada
 * excepción se ajusta, descarta o respalda en su fila; el Excel queda para los
 * cambios en bloque. Las reglas (colores, trabas, avance) están en revision.reglas.
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

  /** Las excepciones en rojo o amarillo: la misma cuenta que pinta la tabla. */
  private readonly pendientes = computed(() => {
    const d = this.d();
    return d ? contarSinCompletar(d.partidas, d.adjuntos, this.store.catalogo()) : 0;
  });

  protected readonly ultimoRechazo = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'rechazo') ?? null);

  protected readonly trabasEnviar = computed(() => (this.d()?.compuertas.enviar ?? []).map((e) => e.mensaje));
  /**
   * Poka-yoke del cierre: además de lo que traba el servidor, no se aprueba con
   * diferencia neta ni con excepciones sin completar, ni lo que uno mismo preparó.
   */
  protected readonly trabasAprobar = computed(() =>
    motivosSinAprobar(
      {
        neta: this.totales().neta,
        sinCompletar: this.pendientes(),
        autor: this.permisos().autor,
        trabas: (this.d()?.compuertas.aprobar ?? []).map((e) => e.mensaje),
      },
      pesos,
    ),
  );

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
    if (this.d()?.pendientes.some((e) => e.codigo === 'SIN_RESPALDO')) lista.push('sin comprobantes');
    return lista;
  });
}
