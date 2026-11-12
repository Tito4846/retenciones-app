import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ControlSegmentado } from './control-segmentado';

@Component({
  imports: [ControlSegmentado],
  template: `<app-control-segmentado [opciones]="opciones" [(valor)]="valor" aria-label="Tipo" />`,
})
class Anfitrion {
  readonly opciones = [
    { id: 'Retencion', etiqueta: 'Retención' },
    { id: 'Percepcion', etiqueta: 'Percepción' },
    { id: 'RetencionPercepcion', etiqueta: 'Ambas' },
  ];
  readonly valor = signal('Retencion');
}

describe('ControlSegmentado', () => {
  function montar() {
    const f = TestBed.createComponent(Anfitrion);
    f.detectChanges();
    const grupo: HTMLElement = f.nativeElement.querySelector('[role=radiogroup]');
    const radios = () => Array.from(grupo.querySelectorAll<HTMLButtonElement>('[role=radio]'));
    return { f, grupo, radios };
  }

  it('marca la opción elegida y solo ella entra con Tab', () => {
    const { radios } = montar();
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    expect(radios().map((r) => r.tabIndex)).toEqual([0, -1, -1]);
  });

  it('el clic y las flechas cambian el valor (y dan la vuelta)', () => {
    const { f, grupo, radios } = montar();
    radios()[1].click();
    expect(f.componentInstance.valor()).toBe('Percepcion');
    grupo.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
    expect(f.componentInstance.valor()).toBe('RetencionPercepcion');
    grupo.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
    expect(f.componentInstance.valor()).toBe('Retencion');
    grupo.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
    expect(f.componentInstance.valor()).toBe('RetencionPercepcion');
  });
});
