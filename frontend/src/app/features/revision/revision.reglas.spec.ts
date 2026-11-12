import { Catalogo, Partida } from '../../core/models/lote.models';
import { contarSinCompletar, estadoFila, leerImporte, motivosSinAprobar, progreso } from './revision.reglas';
import { CONCILIADO, pesos } from './revision.util';

const CATALOGO = { codigos_con_respaldo: ['D01', 'P04'], justificaciones_con_respaldo: ['ajuste', 'error_carga'] } as Catalogo;

function partida(cambios: Partial<Partida> = {}): Partida {
  return {
    clave: 'k1', estado: 'Diferencia de Monto', codigo: 'D01', faltas: [], excluida: 0, adjuntos: 0,
    justificacion: '', comentario: '', importe_fisco: '100.00', importe_erp: '90.00', importe_ajustado: null,
    ...cambios,
  } as Partida;
}

describe('estadoFila', () => {
  it('rojo «Falta acción» cuando falta de un lado y no está completa', () => {
    expect(estadoFila(partida({ codigo: 'N01', faltas: ['Falta elegir'] }), CATALOGO)).toBe('falta_accion');
  });

  it('amarillo cuando es una diferencia sin completar', () => {
    expect(estadoFila(partida({ faltas: ['Falta escribir'] }), CATALOGO)).toBe('en_revision');
  });

  it('rojo «Falta comprobante» si está justificada pero pide respaldo y no tiene', () => {
    expect(estadoFila(partida({ justificacion: 'ajuste', comentario: 'NC 12' }), CATALOGO)).toBe('falta_comprobante');
  });

  it('verde con el comprobante en la fila o un respaldo general del lote', () => {
    const justificada = partida({ justificacion: 'ajuste', comentario: 'NC 12' });
    expect(estadoFila({ ...justificada, adjuntos: 1 }, CATALOGO)).toBe('resuelta');
    expect(estadoFila(justificada, CATALOGO, true)).toBe('resuelta');
  });

  it('descartada y conciliada no cuentan como pendientes', () => {
    const lista = [partida({ excluida: 1, faltas: [] }), partida({ estado: CONCILIADO }), partida({ faltas: ['x'] })];
    expect(contarSinCompletar(lista, [], CATALOGO)).toBe(1);
  });
});

describe('motivosSinAprobar', () => {
  const base = { neta: 0, sinCompletar: 0, autor: false, trabas: [] };

  it('se puede aprobar con diferencia cero y todo completo', () => {
    expect(motivosSinAprobar(base, pesos)).toEqual([]);
  });

  it('traba por diferencia neta, por excepciones y por autoría', () => {
    const motivos = motivosSinAprobar({ neta: 1500, sinCompletar: 2, autor: true, trabas: ['No cuadra'] }, pesos);
    expect(motivos).toHaveLength(4);
    expect(motivos[1]).toContain('15,00');
    expect(motivos[2]).toContain('2 excepciones');
  });
});

describe('progreso', () => {
  it('calcula (total − sin completar) / total', () => {
    expect(progreso(4, 1)).toBe(75);
    expect(progreso(0, 0)).toBe(100);
    expect(progreso(3, 3)).toBe(0);
  });
});

describe('leerImporte', () => {
  it('entiende el formato argentino y el de punto decimal', () => {
    expect(leerImporte('$ 1.234,56')).toBe('1234.56');
    expect(leerImporte('1234.5')).toBe('1234.50');
    expect(leerImporte('1.234')).toBe('1234.00');
    expect(leerImporte('1,234.56')).toBe('1234.56');
    expect(leerImporte('abc')).toBeNull();
    expect(leerImporte('')).toBeNull();
  });
});
