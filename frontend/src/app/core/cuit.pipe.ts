import { Pipe, PipeTransform } from '@angular/core';

/** '20235102162' -> '20-23510216-2'. Lo que no tiene 11 dígitos queda como vino. */
export function formatearCuit(cuit: string | null | undefined): string {
  const digitos = (cuit ?? '').replace(/\D/g, '');
  return digitos.length === 11 ? `${digitos.slice(0, 2)}-${digitos.slice(2, 10)}-${digitos.slice(10)}` : (cuit ?? '');
}

/** La CUIT con los guiones de siempre: se lee de un vistazo con qué contribuyente se opera. */
@Pipe({ name: 'cuit' })
export class CuitPipe implements PipeTransform {
  transform(cuit: string | null | undefined): string {
    return formatearCuit(cuit);
  }
}
