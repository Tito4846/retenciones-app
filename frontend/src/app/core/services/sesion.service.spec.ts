import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { environment } from '../../../environments/environment';
import { SesionService, USUARIOS_DE_PRUEBA, sesionInterceptor } from './sesion.service';

describe('SesionService (sin login: la identidad la da la página principal)', () => {
  function montar() {
    try {
      localStorage.removeItem('retenciones.usuario_prueba');
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

  it('cada pedido a la API lleva el usuario y sus roles; los de afuera no', () => {
    const { http, api } = montar();
    http.get(`${environment.apiUrl}/lotes`).subscribe();
    http.get('https://otro.sitio/x').subscribe();

    const propio = api.expectOne(`${environment.apiUrl}/lotes`).request.headers;
    expect(propio.get('X-Usuario-Id')).toBe('prueba-supervisor');
    expect(propio.get('X-Usuario-Roles')).toBe('supervisor');
    expect(decodeURIComponent(propio.get('X-Usuario-Nombre')!)).toBe('Supervisor de prueba');
    expect(api.expectOne('https://otro.sitio/x').request.headers.has('X-Usuario-Id')).toBe(false);
  });

  it('los roles los confirma el backend y cambiar de usuario los vuelve a pedir', async () => {
    const { sesion, api } = montar();
    const inicio = sesion.iniciar();
    api.expectOne(`${environment.apiUrl}/yo`).flush({ id: 'prueba-supervisor', nombre: 'Supervisor de prueba', roles: ['supervisor'] });
    await inicio;
    expect(sesion.esSupervisor()).toBe(true);

    const empleado = USUARIOS_DE_PRUEBA[0];
    sesion.cambiar(empleado.id);
    const pedido = api.expectOne(`${environment.apiUrl}/yo`);
    expect(pedido.request.headers.get('X-Usuario-Roles')).toBe('empleado');
    pedido.flush(empleado);
    await Promise.resolve();
    await Promise.resolve();
    expect(sesion.esSupervisor()).toBe(false);
    expect(sesion.puedePreparar()).toBe(true);
  });
});
