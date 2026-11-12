import { leerImporte, pestanaInicial } from './conciliacion.estado';

describe('pestanaInicial', () => {
  const resumen = (mayor: number, arca: number) => ({ total_faltan_en_mayor: mayor, total_faltan_en_arca: arca });

  it('arranca en lo que falta, nunca en los conciliados si hay discrepancias', () => {
    expect(pestanaInicial(resumen(3, 2), 1)).toBe('faltan_en_mayor');
    expect(pestanaInicial(resumen(0, 2), 1)).toBe('faltan_en_arca');
  });

  it('sin faltantes: primero los pares dudosos, y si no queda nada, los conciliados', () => {
    expect(pestanaInicial(resumen(0, 0), 2)).toBe('revisar');
    expect(pestanaInicial(resumen(0, 0), 0)).toBe('conciliados');
    expect(pestanaInicial(null, 0)).toBe('conciliados');
  });
});

describe('leerImporte', () => {
  it('lee el formato argentino', () => {
    expect(leerImporte('1.234,56')).toBe(1234.56);
    expect(leerImporte('1234.5')).toBe(1234.5);
    expect(leerImporte('')).toBeNaN();
  });
});
