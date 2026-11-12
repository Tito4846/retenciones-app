import { Routes } from '@angular/router';

/**
 * Sin pantallas de ingreso: quién es y qué roles tiene lo da la página principal
 * (ver core/services/sesion.service.ts). Los permisos se controlan en el backend.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'importar' },
  {
    path: 'importar',
    loadComponent: () => import('./features/importar/importar').then((m) => m.Importar),
  },
  {
    path: 'conciliacion',
    loadComponent: () => import('./features/conciliacion/conciliacion').then((m) => m.Conciliacion),
  },
  {
    // Las dos perspectivas en la misma pantalla: el empleado ve sus lotes para
    // verificar y enviar; el supervisor, las solicitudes de revisión de todos.
    path: 'revision',
    loadComponent: () => import('./features/revision/revision').then((m) => m.RevisionPantalla),
  },
  {
    path: 'conexiones',
    loadComponent: () => import('./features/conexiones/conexiones').then((m) => m.Conexiones),
  },
  // Enlaces viejos (ingresar, configurar, cuenta, usuarios) van al inicio.
  { path: '**', redirectTo: '' },
];
