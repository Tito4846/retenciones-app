import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { Enrolamiento } from '../../core/models/lote.models';
import { CuentasService, mensajeDeError } from '../../core/services/cuentas.service';
import { SesionService } from '../../core/services/sesion.service';
import { ClaveNueva } from '../ingreso/clave-nueva';
import { CodigosRecuperacion } from '../ingreso/codigos';

@Component({
  selector: 'app-mi-cuenta',
  imports: [ClaveNueva, CodigosRecuperacion, DatePipe],
  templateUrl: './cuenta.html',
})
export class MiCuenta implements OnInit {
  private readonly cuentas = inject(CuentasService);
  protected readonly sesion = inject(SesionService);

  protected readonly mensaje = signal<{ ok: boolean; texto: string } | null>(null);
  protected readonly enviando = signal(false);
  protected readonly cambiandoClave = signal(false);
  protected readonly actual = signal('');
  /** Para que el formulario de clave nueva se vacíe después de guardar. */
  protected readonly vuelta = signal(0);
  protected readonly enrolamiento = signal<Enrolamiento | null>(null);
  protected readonly codigos = signal<string[]>([]);
  protected readonly desactivando = signal(false);

  ngOnInit(): void {
    this.cuentas.yo().subscribe();
  }

  private avisar(ok: boolean, texto: string): void {
    this.enviando.set(false);
    this.mensaje.set({ ok, texto });
  }

  protected cambiarClave(nueva: string): void {
    this.enviando.set(true);
    this.cuentas.cambiarClave(this.actual(), nueva).subscribe({
      next: () => {
        this.avisar(true, 'Contraseña cambiada. Se cerraron tus sesiones en otros equipos.');
        this.cambiandoClave.set(false);
        this.actual.set('');
        this.vuelta.update((n) => n + 1);
      },
      error: (e) => this.avisar(false, mensajeDeError(e)),
    });
  }

  protected activar2fa(): void {
    this.mensaje.set(null);
    this.cuentas.iniciarMi2fa().subscribe({
      next: (e) => this.enrolamiento.set(e),
      error: (e) => this.avisar(false, mensajeDeError(e)),
    });
  }

  protected confirmar2fa(codigo: string): void {
    const e = this.enrolamiento();
    if (!e?.desafio) return;
    this.enviando.set(true);
    this.cuentas.confirmarMi2fa(e.desafio, codigo.trim()).subscribe({
      next: (r) => {
        this.enviando.set(false);
        this.enrolamiento.set(null);
        this.codigos.set(r.codigos_recuperacion ?? []);
        this.cuentas.yo().subscribe();
      },
      error: (err) => this.avisar(false, mensajeDeError(err)),
    });
  }

  protected desactivar2fa(clave: string): void {
    this.enviando.set(true);
    this.cuentas.desactivarMi2fa(clave).subscribe({
      next: () => {
        this.avisar(true, 'Segundo factor desactivado.');
        this.desactivando.set(false);
        this.cuentas.yo().subscribe();
      },
      error: (e) => this.avisar(false, mensajeDeError(e)),
    });
  }
}
