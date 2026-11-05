import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';
import { Cuenta, Rol } from '../../core/models/lote.models';
import { CuentasService, mensajeDeError } from '../../core/services/cuentas.service';
import { SesionService } from '../../core/services/sesion.service';

/** Altas, roles, bajas y reseteos. Solo la cuenta principal y quienes ella habilitó. */
@Component({
  selector: 'app-usuarios',
  imports: [DatePipe],
  templateUrl: './usuarios.html',
})
export class Usuarios implements OnInit {
  private readonly cuentas = inject(CuentasService);
  protected readonly sesion = inject(SesionService);

  protected readonly lista = signal<Cuenta[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly ocupado = signal(false);
  protected readonly verInactivos = signal(false);
  /** La contraseña temporal recién generada: se muestra una vez. */
  protected readonly temporal = signal<{ email: string; clave: string; nuevo: boolean } | null>(null);
  protected readonly copiada = signal(false);
  protected readonly confirmar = signal<{ cuenta: Cuenta; accion: 'clave' | '2fa' | 'baja' } | null>(null);

  protected readonly nuevoEmail = signal('');
  protected readonly nuevoNombre = signal('');
  protected readonly nuevoRol = signal<Rol>('empleado');

  protected readonly visibles = computed(() => this.lista().filter((c) => c.activo || this.verInactivos()));
  protected readonly inactivos = computed(() => this.lista().filter((c) => !c.activo).length);

  ngOnInit(): void {
    this.cargar();
  }

  private cargar(): void {
    this.cuentas.listar().subscribe({
      next: (r) => this.lista.set(r.usuarios),
      error: (e) => this.error.set(mensajeDeError(e)),
    });
  }

  private hacer<T>(pedido: Observable<T>, despues?: (r: T) => void): void {
    this.ocupado.set(true);
    this.error.set(null);
    pedido.subscribe({
      next: (r) => {
        this.ocupado.set(false);
        this.confirmar.set(null);
        despues?.(r);
        this.cargar();
      },
      error: (e) => {
        this.ocupado.set(false);
        this.error.set(mensajeDeError(e));
      },
    });
  }

  protected crear(): void {
    this.hacer(this.cuentas.crear(this.nuevoEmail().trim(), this.nuevoNombre().trim(), this.nuevoRol()), (r) => {
      this.temporal.set({ email: r.usuario.email, clave: r.clave_temporal, nuevo: true });
      this.copiada.set(false);
      this.nuevoEmail.set('');
      this.nuevoNombre.set('');
      this.nuevoRol.set('empleado');
    });
  }

  /** Lo que se puede tocar de esa cuenta desde acá. */
  protected editable(c: Cuenta): boolean {
    const yo = this.sesion.cuenta();
    return !c.principal && c.id !== yo?.id && (!c.gestiona_usuarios || !!yo?.principal);
  }

  protected cambiarRol(c: Cuenta, rol: Rol): void {
    this.hacer(this.cuentas.actualizar(c.id, { rol }));
  }

  protected reactivar(c: Cuenta): void {
    this.hacer(this.cuentas.actualizar(c.id, { activo: true }));
  }

  protected alternarGestion(c: Cuenta): void {
    this.hacer(this.cuentas.actualizar(c.id, { gestiona_usuarios: !c.gestiona_usuarios }));
  }

  protected desbloquear(c: Cuenta): void {
    this.hacer(this.cuentas.desbloquear(c.id));
  }

  protected ejecutar(): void {
    const pedido = this.confirmar();
    if (!pedido) return;
    const { cuenta, accion } = pedido;
    if (accion === 'clave') {
      this.hacer(this.cuentas.resetearClave(cuenta.id), (r) => {
        this.temporal.set({ email: cuenta.email, clave: r.clave_temporal, nuevo: false });
        this.copiada.set(false);
      });
    } else if (accion === '2fa') {
      this.hacer(this.cuentas.resetear2fa(cuenta.id));
    } else {
      this.hacer(this.cuentas.actualizar(cuenta.id, { activo: false }));
    }
  }

  protected copiar(texto: string): void {
    navigator.clipboard?.writeText(texto).then(() => this.copiada.set(true));
  }
}
