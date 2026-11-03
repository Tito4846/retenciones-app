import { RenderMode, ServerRoute } from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
  {
    // Herramienta local contra un backend local: todo se arma en el navegador.
    // Renderizar en el servidor no aporta nada y, como HttpClient usa XHR (por
    // el progreso de subida), Angular avisaba NG02801 y abortaba renders.
    path: '**',
    renderMode: RenderMode.Client,
  },
];
