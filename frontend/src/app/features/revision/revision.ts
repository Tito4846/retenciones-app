import { DatePipe } from '@angular/common';
import { Component, computed, inject, linkedSignal, signal } from '@angular/core';
import { Observacion, Partida } from '../../core/models/lote.models';
import { LotesStore } from '../../core/services/lotes.store';
import { SesionService } from '../../core/services/sesion.service';
import { AccionesLote } from './acciones-lote';
import { BandejaLotes } from './bandeja-lotes';
import { ControlesLote } from './controles-lote';
import { HistorialLote } from './historial-lote';
import { MarcadorCruce } from './marcador-cruce';
import { AdjuntosPorPartida, centavos, CONCILIADO, ESTADOS, importeSistema, ObservacionesPorPartida, periodo, Permisos, pesos } from './revision.util';
import { EdicionExcepcion, TarjetaExcepcion } from './tarjeta-excepcion';
import { VersionesLote } from './versiones-lote';

type Pestana = 'excepciones' | 'conciliadas' | 'controles' | 'versiones' | 'historial';

/** Las trabas que la pantalla ya calcula sola: del resto se ocupa lo que diga el backend. */
const TRABAS_LOCALES = new Set(['DIFERENCIA_NETA', 'SIN_JUSTIFICACION', 'SIN_COMENTARIO', 'SIN_RESPALDO']);

/**
 * Verificación y aprobación: bandeja a la izquierda, lote a la derecha. Manejo
 * por excepción: se trabaja solo lo que no concilió; lo demás queda en su pestaña.
 */
@Component({
  selector: 'app-revision',
  imports: [AccionesLote, BandejaLotes, ControlesLote, DatePipe, HistorialLote, MarcadorCruce, TarjetaExcepcion, VersionesLote],
  templateUrl: './revision.html',
})
export class RevisionPantalla {
  protected readonly store = inject(LotesStore);
  protected readonly sesion = inject(SesionService);
  protected readonly estados = ESTADOS;
  protected readonly pesos = pesos;
  protected readonly periodo = periodo;

  protected readonly d = this.store.detalle;
  protected readonly seleccionId = signal<string | null>(null);
  private readonly loteId = computed(() => this.d()?.lote.id ?? null);

  protected readonly pestana = linkedSignal<string | null, Pestana>({ source: this.loteId, computation: () => 'excepciones' });
  protected readonly busqueda = linkedSignal({ source: this.loteId, computation: () => '' });
  protected readonly soloPendientes = linkedSignal({ source: this.loteId, computation: () => false });
  /** Importes a medio escribir, por partida: mueven el marcador antes de guardarse. */
  private readonly borradores = linkedSignal<string | null, Record<string, string | null>>({ source: this.loteId, computation: () => ({}) });

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

  private readonly vivas = computed(() => (this.d()?.partidas ?? []).filter((p) => !p.excluida));
  /** Solo lo que no concilió: es lo único que se trabaja. */
  private readonly todasLasExcepciones = computed(() => this.vivas().filter((p) => p.estado !== CONCILIADO));
  protected readonly quitadas = computed(() => (this.d()?.partidas ?? []).filter((p) => p.excluida));
  protected readonly conciliadas = computed(() => this.vivas().filter((p) => p.estado === CONCILIADO));
  protected readonly pendientes = computed(() => this.todasLasExcepciones().filter((p) => p.faltas.length));

  protected readonly excepciones = computed(() => {
    const texto = this.busqueda().trim().toLowerCase();
    const base = this.soloPendientes() ? this.pendientes() : this.todasLasExcepciones();
    if (!texto) return base;
    return base.filter((p) =>
      `${p.razon_social} ${p.cuit} ${p.certificado} ${p.documento_erp} ${p.impuesto} ${p.importe_fisco ?? ''} ${p.importe_erp ?? ''}`.toLowerCase().includes(texto),
    );
  });

  /** Los totales en centavos, contando lo que se está escribiendo. */
  protected readonly totales = computed(() => {
    const borradores = this.borradores();
    let sistema = 0;
    let declarado = 0;
    for (const p of this.vivas()) {
      sistema += centavos(importeSistema(p, borradores[p.clave]));
      declarado += centavos(p.importe_fisco);
    }
    return { sistema, declarado, neta: declarado - sistema };
  });

  protected readonly adjuntosPorPartida = computed(() => {
    const mapa: AdjuntosPorPartida = {};
    for (const a of this.d()?.adjuntos ?? []) if (a.clave_partida) (mapa[a.clave_partida] ??= []).push(a);
    return mapa;
  });
  protected readonly obsPorPartida = computed(() => {
    const mapa: ObservacionesPorPartida = {};
    for (const o of this.d()?.observaciones ?? []) if (o.clave_partida) (mapa[o.clave_partida] ??= []).push(o);
    return mapa;
  });
  protected readonly observacionesAbiertas = computed(() => (this.d()?.observaciones ?? []).filter((o) => !o.resuelta_utc));

  protected readonly ultimoRechazo = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'rechazo') ?? null);
  protected readonly ultimoEnvio = computed(() => [...(this.d()?.decisiones ?? [])].reverse().find((x) => x.tipo === 'envio') ?? null);
  /** El motivo del rechazo sin las líneas que ya están como observaciones (esas se listan aparte). */
  protected readonly motivoRechazo = computed(() => {
    const textos = this.observacionesAbiertas().map((o) => o.texto);
    const resto = (this.ultimoRechazo()?.motivo ?? '')
      .split('\n')
      .filter((linea) => !textos.some((t) => linea.includes(t)))
      .join('\n')
      .trim();
    return resto === 'Corregir:' ? '' : resto;
  });

  /** Lo que bloquea el botón principal: se recalcula con cada tecla, no al guardar. */
  private readonly trabasComunes = computed(() => {
    const trabas: string[] = [];
    if (this.totales().neta) trabas.push(`la diferencia neta es ${pesos(this.totales().neta / 100)}`);
    const n = this.pendientes().length;
    if (n) trabas.push(`faltan completar ${n} ${n === 1 ? 'excepción' : 'excepciones'}`);
    return trabas;
  });
  protected readonly trabasEnviar = computed(() => [
    ...this.trabasComunes(),
    ...(this.d()?.compuertas.enviar ?? []).filter((e) => !TRABAS_LOCALES.has(e.codigo)).map((e) => e.mensaje),
  ]);
  protected readonly trabasAprobar = computed(() => [
    ...this.trabasComunes(),
    ...(this.d()?.compuertas.aprobar ?? []).filter((e) => !TRABAS_LOCALES.has(e.codigo)).map((e) => (e.codigo === 'CONTROLES' ? this.faltanControles() : e.mensaje)),
  ]);

  protected readonly controlesFaltantes = computed(() => {
    const det = this.d();
    return Object.keys(this.store.catalogo()?.controles ?? {}).filter((c) => !det?.controles[c]?.ok).length;
  });

  private faltanControles(): string {
    const n = this.controlesFaltantes();
    return `falta${n === 1 ? '' : 'n'} tildar ${n} control${n === 1 ? '' : 'es'}`;
  }

  /** Mientras se escribe: mueve el marcador, todavía sin guardar. */
  protected anotarImporte(cambio: { clave: string; importe: string | null }): void {
    this.borradores.update((b) => ({ ...b, [cambio.clave]: cambio.importe }));
  }

  /** Guarda lo editado en una tarjeta. La diferencia neta sale de la respuesta del backend. */
  protected actualizarExcepcion({ clave, valores, revertir }: EdicionExcepcion): void {
    const det = this.d();
    if (!det) return;
    this.store.actualizarPartida(det.lote.id, clave, det.lote.version_vigente, valores).subscribe((ok) => {
      if (!ok) revertir?.();
      this.borradores.update(({ [clave]: _, ...resto }) => resto);
    });
  }

  protected restaurar(p: Partida): void {
    this.actualizarExcepcion({ clave: p.clave, valores: { excluida: false } });
  }

  protected irAObservacion(o: Observacion): void {
    this.soloPendientes.set(false);
    this.busqueda.set('');
    this.pestana.set('excepciones');
    setTimeout(() => document.getElementById('partida-' + o.clave_partida)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }

  protected partidaDe(clave: string | null): Partida | undefined {
    return clave ? this.d()?.partidas.find((p) => p.clave === clave) : undefined;
  }
}
