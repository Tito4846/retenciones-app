import { ApplicationConfig, LOCALE_ID, provideBrowserGlobalErrorListeners } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import { provideHttpClient, withXhr } from '@angular/common/http';
import localeEsAr from '@angular/common/locales/es-AR';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';

registerLocaleData(localeEsAr);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    // XHR (no fetch) para poder reportar el progreso de subida de archivos.
    provideHttpClient(withXhr()),
    { provide: LOCALE_ID, useValue: 'es-AR' },
  ]
};
