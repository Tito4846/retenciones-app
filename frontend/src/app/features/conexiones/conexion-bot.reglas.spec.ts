import { ConexionArca, ConexionXubio } from '../../core/models/conexiones.models';
import { cuandoLegible, etiquetaArca, saludArca, saludXubio } from './conexion-bot.reglas';

const arca = (cambios: Partial<ConexionArca>) => ({ estado: 'desconectado', mensaje: null, ...cambios }) as ConexionArca;
const xubio = (cambios: Partial<ConexionXubio>) => ({ estado: 'desconectado', mensaje: null, ...cambios }) as ConexionXubio;

describe('saludArca', () => {
  it('distingue error de desconexión a pedido', () => {
    expect(saludArca(arca({ mensaje: 'Se cerró la ventana de ARCA' }))).toBe('error');
    expect(saludArca(arca({}))).toBe('sin_conectar');
    expect(saludArca(null)).toBe('sin_conectar');
  });

  it('pide acción cuando el bot espera el doble factor en la ventana', () => {
    expect(saludArca(arca({ estado: 'conectando', mensaje: 'Si ARCA pide el doble factor, completalo en la ventana de Chrome (hasta 120s)…' }))).toBe('requiere_accion');
    expect(saludArca(arca({ estado: 'conectando', mensaje: 'Entrando a ARCA…' }))).toBe('en_curso');
  });

  it('muestra «Renovando sesión…» sin perder el verde', () => {
    const c = arca({ estado: 'conectado', renovando: true });
    expect(saludArca(c)).toBe('conectado');
    expect(etiquetaArca(c)).toBe('Renovando sesión…');
  });
});

describe('saludXubio', () => {
  it('amarillo con el captcha pedido, azul mientras el bot escribe', () => {
    expect(saludXubio(xubio({ estado: 'esperando_ingreso', mensaje: 'Xubio pidió el captcha: resolvelo en la ventana de Chrome del bot.' }))).toBe('requiere_accion');
    expect(saludXubio(xubio({ estado: 'esperando_ingreso', mensaje: 'El bot ya puso el mail y la contraseña de Xubio: esperando que Xubio deje entrar…' }))).toBe('en_curso');
    expect(saludXubio(xubio({ mensaje: 'No se entró a Xubio a tiempo: se cerró la ventana.' }))).toBe('error');
  });
});

describe('cuandoLegible', () => {
  const ahora = new Date(2026, 10, 12, 15, 0);
  it('dice hoy, ayer o la fecha', () => {
    expect(cuandoLegible(new Date(2026, 10, 12, 10, 45).toISOString(), ahora)).toBe('Hoy 10:45');
    expect(cuandoLegible(new Date(2026, 10, 11, 18, 2).toISOString(), ahora)).toBe('Ayer 18:02');
    expect(cuandoLegible(new Date(2026, 10, 3, 9, 15).toISOString(), ahora)).toBe('03/11 09:15');
    expect(cuandoLegible(null, ahora)).toBe('');
  });
});
