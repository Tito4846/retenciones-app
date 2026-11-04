import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, catchError, of, switchMap, timer } from 'rxjs';
import { environment } from '../../../environments/environment';
import { ConsultaArca, Conexiones } from '../models/conexiones.models';

const INTERVALO_MS = 2000;

/**
 * Estado de los bots de ARCA y Xubio. Las pantallas lo siguen mientras están
 * abiertas (`seguir`); las credenciales solo viajan en el POST de conectar y
 * nunca vuelven en el estado.
 */
@Injectable({ providedIn: 'root' })
export class ConexionesStore {
  private readonly http = inject(HttpClient);
  private readonly api = `${environment.apiUrl}/conexiones`;

  readonly conexiones = signal<Conexiones | null>(null);
  /** El backend no contesta (apagado o sin red). */
  readonly sinServidor = signal(false);
  readonly error = signal<string | null>(null);
  readonly enviando = signal(false);

  readonly arca = computed(() => this.conexiones()?.arca ?? null);
  readonly xubio = computed(() => this.conexiones()?.xubio ?? null);
  readonly listasParaConciliar = computed(
    () => !!this.arca()?.descarga && this.xubio()?.estado === 'conectado',
  );

  /**
   * Consulta el estado cada 2 s hasta que se destruya quien lo pidió. Se
   * llama desde afterNextRender: en el prerender (SSR) no hay backend.
   */
  seguir(destroyRef: DestroyRef): void {
    timer(0, INTERVALO_MS)
      .pipe(
        switchMap(() =>
          this.http.get<Conexiones>(this.api).pipe(catchError(() => of(null))),
        ),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((conexiones) => {
        this.sinServidor.set(conexiones === null);
        if (conexiones) this.conexiones.set(conexiones);
      });
  }

  conectarArca(cuit: string, clave: string): void {
    this.enviar(this.http.post<Conexiones>(`${this.api}/arca`, { cuit, clave }));
  }

  /** El bot completa el formulario de Mis Retenciones; el avance llega por el estado. */
  consultarArca(consulta: ConsultaArca): void {
    this.enviar(this.http.post<Conexiones>(`${this.api}/arca/consultar`, consulta));
  }

  /** Corta «Solo consultar ARCA»: el bot termina el impuesto en curso y no sigue. */
  cancelarConsultaArca(): void {
    this.enviar(this.http.post<Conexiones>(`${this.api}/arca/cancelar`, {}));
  }

  desconectarArca(): void {
    this.enviar(this.http.delete<Conexiones>(`${this.api}/arca`));
  }

  conectarXubio(usuario: string | null, clave: string | null): void {
    this.enviar(this.http.post<Conexiones>(`${this.api}/xubio`, { usuario, clave }));
  }

  desconectarXubio(): void {
    this.enviar(this.http.delete<Conexiones>(`${this.api}/xubio`));
  }

  /** Trae al frente la ventana del bot (para exportar o resolver el captcha). */
  mostrar(cual: 'arca' | 'xubio'): void {
    this.http
      .post<void>(`${this.api}/${cual}/mostrar`, {})
      .subscribe({ error: (e: unknown) => this.error.set(this.mensaje(e)) });
  }

  private enviar(pedido: Observable<Conexiones>): void {
    this.enviando.set(true);
    this.error.set(null);
    pedido.subscribe({
      next: (conexiones) => {
        this.conexiones.set(conexiones);
        this.enviando.set(false);
      },
      error: (e: unknown) => {
        this.error.set(this.mensaje(e));
        this.enviando.set(false);
      },
    });
  }

  private mensaje(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const detalle = error.error?.detail;
      if (typeof detalle === 'string') return detalle;
      if (Array.isArray(detalle)) return 'Revisá los datos ingresados.';
      if (error.status === 0) return 'No se pudo hablar con el servidor.';
    }
    return 'Ocurrió un error inesperado.';
  }
}
