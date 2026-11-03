import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'importar' },
  {
    path: 'importar',
    loadComponent: () => import('./features/importar/importar').then((m) => m.Importar),
  },
  {
    path: 'conciliacion',
    loadComponent: () =>
      import('./features/conciliacion/conciliacion').then((m) => m.Conciliacion),
  },
  {
    path: 'conexiones',
    loadComponent: () => import('./features/conexiones/conexiones').then((m) => m.Conexiones),
  },
];
