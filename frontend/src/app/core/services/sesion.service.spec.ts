import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { environment } from '../../../environments/environment';
import { SesionService, USUARIOS_DE_PRUEBA, sesionInterceptor } from './sesion.service';

describe('SesionService (la identidad y el nivel los da ContadorWeb)', () => {
  function montar() {
    try {
      localStorage.removeItem('retenciones.usuario_prueba');
      sessionStorage.removeItem('retenciones.sesion');
    } catch {
      /* sin almacenamiento */
    }
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([sesionInterceptor])), provideHttpClientTesting()],
    });
    return {
      sesion: TestBed.inject(SesionService),
      http: TestBed.inject(HttpClient),
      api: TestBed.inject(HttpTestingController),
    };
  }

  afterEach(() => window.history.replaceState(null, '', '/'));

  it('modo local: cada pedido a la API lleva el usuario de prueba y su nivel; los de afuera no', () => {
    const { http, api } = montar();
    http.get(`${environment.apiUrl}/lotes`).subscribe();
    http.get('https://otro.sitio/x').subscribe();

    const propio = api.expectOne(`${environment.apiUrl}/lotes`).request.headers;
    expect(propio.get('X-Usuario-Id')).toBe('prueba-admin');
    expect(propio.get('X-Usuario-Nivel')).toBe('100');
    expect(decodeURIComponent(propio.get('X-Usuario-Nombre')!)).toBe('Administrador de prueba');
    expect(propio.has('Authorization')).toBe(false);
    expect(api.expectOne('https://otro.sitio/x').request.headers.has('X-Usuario-Id')).toBe(false);
  });

  it('modo local: los roles los confirma el backend y cambiar de usuario los vuelve a pedir', async () => {
    const { sesion, api } = montar();
    const inicio = sesion.iniciar();
    api.expectOne(`${environment.apiUrl}/yo`).flush({ id: 'prueba-admin', nombre: 'Administrador de prueba', roles: ['supervisor'], conectado: false });
    await inicio;
    expect(sesion.esSupervisor()).toBe(true);
    expect(sesion.conectado()).toBe(false);

    const usuario = USUARIOS_DE_PRUEBA.find((u) => u.nivel === '10')!;
    sesion.cambiar(usuario.id);
    const pedido = api.expectOne(`${environment.apiUrl}/yo`);
    expect(pedido.request.headers.get('X-Usuario-Nivel')).toBe('10');
    expect(decodeURIComponent(pedido.request.headers.get('X-Usuario-Modulos')!)).toBe('Impuestos/Retenciones');
    pedido.flush({ ...usuario, roles: ['empleado'] });
    await Promise.resolve();
    await Promise.resolve();
    expect(sesion.esSupervisor()).toBe(false);
    expect(sesion.puedePreparar()).toBe(true);
  });

  it('con el pase de ContadorWeb abre la sesión y manda el token en vez del usuario de prueba', async () => {
    window.history.replaceState(null, '', '/importar?pase=abc.def');
    const { sesion, http, api } = montar();
    const inicio = sesion.iniciar();

    const entrar = api.expectOne(`${environment.apiUrl}/sesion/entrar`);
    expect(entrar.request.body).toEqual({ pase: 'abc.def' });
    expect(window.location.search).toBe('');
    entrar.flush({ token: 'tok', expira: Date.now() / 1000 + 3600, usuario: {} });
    await Promise.resolve();
    await Promise.resolve();

    const yo = api.expectOne(`${environment.apiUrl}/yo`);
    expect(yo.request.headers.get('Authorization')).toBe('Bearer tok');
    expect(yo.request.headers.has('X-Usuario-Id')).toBe(false);
    yo.flush({ id: 'carlos', nombre: 'Carlos', roles: ['supervisor'], nivel: '200', nivel_nombre: 'Administrador General', conectado: true });
    await inicio;
    expect(sesion.conectado()).toBe(true);
    expect(sesion.esSupervisor()).toBe(true);

    http.get(`${environment.apiUrl}/lotes`).subscribe();
    expect(api.expectOne(`${environment.apiUrl}/lotes`).request.headers.get('Authorization')).toBe('Bearer tok');
  });

  it('sin acceso en ContadorWeb muestra el motivo y no entra', async () => {
    const { sesion, api } = montar();
    const inicio = sesion.iniciar();
    api.expectOne(`${environment.apiUrl}/yo`).flush(
      { detail: { codigo: 'SIN_ROL', mensaje: 'No incluye Impuestos › Retenciones.' } },
      { status: 403, statusText: 'Forbidden' },
    );
    await inicio;
    expect(sesion.usuario()).toBeNull();
    expect(sesion.sinSesion()).toBe('No incluye Impuestos › Retenciones.');
  });
});
