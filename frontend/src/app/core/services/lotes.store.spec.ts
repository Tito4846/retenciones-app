import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { EstadoLote, LoteListado } from '../models/lote.models';
import { LotesStore } from './lotes.store';
import { SesionService } from './sesion.service';

const EMPLEADO = 'u-empleado';
const SUPERVISOR = 'u-supervisor';

function lote(estado: EstadoLote, cambios: Partial<LoteListado> = {}): LoteListado {
  return { estado, maker_id: EMPLEADO, autores: [EMPLEADO], checker_id: null, ...cambios } as LoteListado;
}

function store(rol: 'empleado' | 'supervisor'): LotesStore {
  const supervisor = rol === 'supervisor';
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideRouter([]),
      {
        provide: SesionService,
        useValue: {
          // Sin usuario: el store no pide la bandeja al crearse.
          usuario: signal(null),
          puedePreparar: computed(() => true),
          esSupervisor: computed(() => supervisor),
        },
      },
    ],
  });
  return TestBed.inject(LotesStore);
}

describe('LotesStore · Mis Tareas', () => {
  it('un lote enviado que nadie tomó queda en «Esperando Revisión» para el supervisor', () => {
    const s = store('supervisor');
    const enviado = lote('pendiente');
    expect(s.enMisTareas(enviado, SUPERVISOR)).toBe(false);
    // Igual le avisa que hay uno nuevo para revisar.
    expect(s.esperaA(enviado, SUPERVISOR)).toBe(true);
  });

  it('al tomarlo pasa a «Mis Tareas» del supervisor que lo revisa, no de otro', () => {
    const s = store('supervisor');
    const tomado = lote('en_revision', { checker_id: SUPERVISOR });
    expect(s.enMisTareas(tomado, SUPERVISOR)).toBe(true);
    expect(s.enMisTareas(tomado, 'otro-supervisor')).toBe(false);
  });

  it('el empleado tiene en «Mis Tareas» sus borradores y rechazados, no lo enviado', () => {
    const s = store('empleado');
    expect(s.enMisTareas(lote('borrador'), EMPLEADO)).toBe(true);
    expect(s.enMisTareas(lote('rechazado'), EMPLEADO)).toBe(true);
    expect(s.enMisTareas(lote('pendiente'), EMPLEADO)).toBe(false);
  });
});
