import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Observable } from 'rxjs';
import { Enrolamiento, PasoIngreso } from '../../core/models/lote.models';
import { CuentasService, mensajeDeError } from '../../core/services/cuentas.service';
import { ClaveNueva } from './clave-nueva';
import { CodigosRecuperacion } from './codigos';

type Paso = 'credenciales' | '2fa' | 'cambiar_clave' | 'enrolar_2fa' | 'codigos';

@Component({
  selector: 'app-ingreso',
  imports: [ClaveNueva, CodigosRecuperacion],
  templateUrl: './ingreso.html',
})
export class Ingreso implements OnInit {
  private readonly cuentas = inject(CuentasService);
  private readonly router = inject(Router);
  private readonly ruta = inject(ActivatedRoute);

  protected readonly paso = signal<Paso>('credenciales');
  protected readonly email = signal('');
  protected readonly error = signal<string | null>(null);
  protected readonly enviando = signal(false);
  protected readonly principalSinConfigurar = signal<string | null>(null);
  protected readonly enrolamiento = signal<Enrolamiento | null>(null);
  protected readonly codigos = signal<string[]>([]);
  protected readonly avisoRecuperacion = signal<number | null>(null);
  private desafio = '';

  ngOnInit(): void {
    this.cuentas.estado().subscribe({
      next: (e) => this.principalSinConfigurar.set(e.configurada ? null : e.cuenta_principal),
      error: () => this.error.set('No hay conexión con el servidor. ¿Está levantado el backend?'),
    });
    const email = this.ruta.snapshot.queryParamMap.get('email');
    if (email) this.email.set(email);
  }

  private enviar(pedido: Observable<PasoIngreso>): void {
    this.error.set(null);
    this.enviando.set(true);
    pedido.subscribe({
      next: (r) => {
        this.enviando.set(false);
        this.seguir(r);
      },
      error: (e) => {
        this.enviando.set(false);
        this.error.set(mensajeDeError(e));
        // El desafío venció: de vuelta al principio.
        if (e?.error?.detail?.codigo === 'DESAFIO_VENCIDO' || e?.error?.detail?.codigo === 'PASO_INCORRECTO') this.paso.set('credenciales');
      },
    });
  }

  private seguir(r: PasoIngreso): void {
    if (r.desafio) this.desafio = r.desafio;
    if (r.codigos_recuperacion_restantes !== undefined) this.avisoRecuperacion.set(r.codigos_recuperacion_restantes);
    if (r.paso === 'listo') {
      if (r.codigos_recuperacion?.length) {
        this.codigos.set(r.codigos_recuperacion);
        this.paso.set('codigos');
      } else {
        this.entrar();
      }
      return;
    }
    this.paso.set(r.paso);
    if (r.paso === 'enrolar_2fa') {
      this.enrolamiento.set(null);
      this.cuentas.enrolar(this.desafio).subscribe({
        next: (e) => this.enrolamiento.set(e),
        error: (e) => this.error.set(mensajeDeError(e)),
      });
    }
  }

  protected entrar(): void {
    const volver = this.ruta.snapshot.queryParamMap.get('volver');
    // Solo rutas internas: un enlace armado no puede mandar a otro sitio.
    this.router.navigateByUrl(volver && volver.startsWith('/') && !volver.startsWith('//') ? volver : '/');
  }

  protected ingresar(email: string, clave: string): void {
    this.email.set(email.trim());
    this.enviar(this.cuentas.ingresar(email.trim(), clave));
  }

  protected verificar(codigo: string): void {
    this.enviar(this.cuentas.segundoFactor(this.desafio, codigo.trim()));
  }

  protected cambiarClave(nueva: string): void {
    this.enviar(this.cuentas.cambiarClaveInicial(this.desafio, nueva));
  }

  protected confirmar(codigo: string): void {
    this.enviar(this.cuentas.confirmarEnrolamiento(this.desafio, codigo.trim()));
  }

  protected volverAEmpezar(): void {
    this.error.set(null);
    this.paso.set('credenciales');
  }
}
