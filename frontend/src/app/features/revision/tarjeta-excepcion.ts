import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, linkedSignal, output, signal } from '@angular/core';
import { Adjunto, Observacion, OrigenObservacion, Partida } from '../../core/models/lote.models';
import { CambioPartida, LotesStore } from '../../core/services/lotes.store';
import { centavos, JUSTIFICACION_CORTA, leerImporte, mostrarImporte, ORDEN_ORIGENES, ORIGENES, pesos } from './revision.util';

export interface EdicionExcepcion {
  clave: string;
  valores: CambioPartida;
  /** Si no se pudo guardar, el campo vuelve a mostrar lo guardado. */
  revertir?: () => void;
}

/** Una partida que no concilió sola: se corrige acá mismo. */
@Component({
  selector: 'app-tarjeta-excepcion',
  imports: [DatePipe],
  templateUrl: './tarjeta-excepcion.html',
})
export class TarjetaExcepcion {
  protected readonly store = inject(LotesStore);
  protected readonly pesos = pesos;
  protected readonly origenes = ORIGENES;
  protected readonly ordenOrigenes = ORDEN_ORIGENES;
  protected readonly justificacionCorta = JUSTIFICACION_CORTA;

  readonly partida = input.required<Partida>();
  readonly loteId = input.required<string>();
  readonly version = input.required<number>();
  readonly adjuntos = input<Adjunto[]>([]);
  readonly observaciones = input<Observacion[]>([]);
  /** Puede corregir: quien prepara, o el supervisor que lo revisa. */
  readonly editable = input(false);
  /** Puede dejar observaciones: solo el supervisor que lo revisa. */
  readonly observable = input(false);

  /** Mientras se escribe el importe: para que el marcador se mueva antes de guardar. */
  readonly vista = output<{ clave: string; importe: string | null }>();
  readonly cambio = output<EdicionExcepcion>();

  protected readonly justificaciones = computed(() => Object.entries(this.store.catalogo()?.justificaciones ?? {}));
  protected readonly motivo = computed(() => this.store.catalogo()?.codigos?.[this.partida().codigo] ?? this.partida().estado);
  protected readonly flags = computed(() => this.partida().flags.map((f) => this.store.catalogo()?.flags?.[f] ?? f));

  protected readonly textoImporte = linkedSignal(() => mostrarImporte(this.partida().importe_ajustado));
  private readonly importeEscrito = computed(() => leerImporte(this.textoImporte()));
  protected readonly importeInvalido = computed(() => this.importeEscrito() === undefined);

  /** ARCA contra lo que cuenta en el sistema: el ajuste si lo hay, si no el Mayor. */
  protected readonly diferencia = computed(() => {
    const p = this.partida();
    const escrito = this.importeEscrito();
    const sistema = escrito === undefined || escrito === null ? p.importe_erp : escrito;
    return (centavos(p.importe_fisco) - centavos(sistema)) / 100;
  });

  protected readonly quitando = signal(false);
  protected readonly motivoQuitar = signal('');
  protected readonly observando = signal(false);
  protected readonly textoObservacion = signal('');
  protected readonly origenObservacion = signal<OrigenObservacion>('nuestro');

  protected escribirImporte(texto: string): void {
    this.textoImporte.set(texto);
    const importe = leerImporte(texto);
    if (importe !== undefined) this.vista.emit({ clave: this.partida().clave, importe });
  }

  protected guardarImporte(): void {
    const importe = this.importeEscrito();
    if (importe === undefined) return;
    const p = this.partida();
    if (centavos(importe) === centavos(p.importe_ajustado) && !importe === !p.importe_ajustado) return;
    this.cambio.emit({ clave: p.clave, valores: { importe_ajustado: importe }, revertir: () => this.textoImporte.set(mostrarImporte(p.importe_ajustado)) });
  }

  protected igualarArca(): void {
    const p = this.partida();
    const importe = p.importe_fisco ?? '0.00';
    this.textoImporte.set(mostrarImporte(importe));
    this.vista.emit({ clave: p.clave, importe });
    this.cambio.emit({ clave: p.clave, valores: { importe_ajustado: importe }, revertir: () => this.textoImporte.set(mostrarImporte(p.importe_ajustado)) });
  }

  protected justificar(valor: string): void {
    if (valor !== this.partida().justificacion) this.cambio.emit({ clave: this.partida().clave, valores: { justificacion: valor } });
  }

  protected comentar(campo: HTMLTextAreaElement): void {
    const p = this.partida();
    if (campo.value.trim() === p.comentario) return;
    this.cambio.emit({ clave: p.clave, valores: { comentario: campo.value }, revertir: () => (campo.value = p.comentario) });
  }

  protected quitar(): void {
    const motivo = this.motivoQuitar().trim();
    if (!motivo) return;
    this.cambio.emit({ clave: this.partida().clave, valores: { excluida: true, comentario: motivo } });
    this.quitando.set(false);
  }

  protected adjuntar(evento: Event): void {
    const entrada = evento.target as HTMLInputElement;
    const archivo = entrada.files?.[0];
    entrada.value = '';
    if (archivo) this.store.subirAdjunto(this.loteId(), archivo, this.partida().clave).subscribe();
  }

  protected observar(): void {
    const texto = this.textoObservacion().trim();
    if (!texto) return;
    this.store.observar(this.loteId(), this.version(), texto, this.origenObservacion(), this.partida().clave).subscribe((ok) => {
      if (ok) {
        this.textoObservacion.set('');
        this.observando.set(false);
      }
    });
  }
}
