import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { SesionService } from './sesion.service';

/** Pantallas de trabajo: sin sesión, a ingresar (y después vuelve a donde iba). */
export const conSesion: CanActivateFn = (_, estado) => {
  const sesion = inject(SesionService);
  return sesion.usuario() ? true : inject(Router).createUrlTree(['/ingresar'], { queryParams: { volver: estado.url } });
};

/** Ingresar y configurar: con sesión ya abierta no tiene sentido. */
export const sinSesion: CanActivateFn = () => (inject(SesionService).usuario() ? inject(Router).createUrlTree(['/']) : true);

/** Usuarios: solo la cuenta principal y quienes ella habilitó. */
export const gestionaUsuarios: CanActivateFn = () =>
  inject(SesionService).gestionaUsuarios() ? true : inject(Router).createUrlTree(['/']);