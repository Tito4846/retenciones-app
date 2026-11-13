import { ApplicationConfig, LOCALE_ID, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import { provideHttpClient, withInterceptors, withXhr } from '@angular/common/http';
import localeEsAr from '@angular/common/locales/es-AR';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { SesionService, sesionInterceptor } from './core/services/sesion.service';

registerLocaleData(localeEsAr);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    // XHR (no fetch) para poder reportar el progreso de subida de archivos.
    provideHttpClient(withXhr(), withInterceptors([sesionInterceptor])),
    // Antes de la primera pantalla: canjea el pase de ContadorWeb o retoma la sesión guardada.
    provideAppInitializer(() => inject(SesionService).iniciar()),
    { provide: LOCALE_ID, useValue: 'es-AR' },
  ]
};
