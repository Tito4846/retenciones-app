import { RenderMode, ServerRoute } from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
  {
    // Todo se arma en el navegador: con HttpClient sobre XHR (progreso de subida)
    // el render en el servidor avisaba NG02801 y abortaba.
    path: '**',
    renderMode: RenderMode.Client,
  },
];
