import { Routes } from '@angular/router';
import { conSesion, gestionaUsuarios, sinSesion } from './core/services/guardias';

export const routes: Routes = [
  {
    path: 'ingresar',
    canActivate: [sinSesion],
    loadComponent: () => import('./features/ingreso/ingreso').then((m) => m.Ingreso),
  },
  {
    path: 'configurar',
    canActivate: [sinSesion],
    loadComponent: () => import('./features/ingreso/configurar').then((m) => m.Configurar),
  },
  {
    path: '',
    canActivateChild: [conSesion],
    children: [
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
      {
        path: 'cuenta',
        loadComponent: () => import('./features/cuenta/cuenta').then((m) => m.MiCuenta),
      },
      {
        path: 'usuarios',
        canActivate: [gestionaUsuarios],
        loadComponent: () => import('./features/usuarios/usuarios').then((m) => m.Usuarios),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
