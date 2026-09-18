import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReposBuscarRow, ReposBuscadosComponent } from './repos-buscados';

function rows(): ReposBuscarRow[] {
  return [
    { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', tags: [] },
    { slug: 'r2', name: 'R2', workspace: 'ws', default_branch: 'master', tags: [] },
  ];
}

async function mount() {
  TestBed.configureTestingModule({ imports: [ReposBuscadosComponent] });
  await TestBed.compileComponents();

  const fixture = TestBed.createComponent(ReposBuscadosComponent);
  fixture.componentRef.setInput('repos', rows());
  return { fixture, component: fixture.componentInstance };
}

describe('ReposBuscadosComponent — columna PR y botón ver rama', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  it('la columna PR no aparece hasta ejecutar ver rama', async () => {
    const { fixture, component } = await mount();
    fixture.componentRef.setInput('origin', 'release/x');
    expect(component.mostrarPr()).toBe(false);

    component.onToggleProcesarSalida(true);
    expect(component.mostrarPr()).toBe(false);

    component.onVerRama();
    expect(component.mostrarPr()).toBe(true);
  });

  it('ver rama está deshabilitado sin rama origen', async () => {
    const { fixture, component } = await mount();
    component.onToggleProcesarSalida(true);
    expect(component.puedeVerRama()).toBe(false);

    fixture.componentRef.setInput('origin', 'release/x');
    fixture.componentRef.setInput('destination', 'master');
    expect(component.puedeVerRama()).toBe(true);
  });

  it('onVerRama no emite ni muestra PR si no está habilitado', async () => {
    const { fixture, component } = await mount();
    component.onToggleProcesarSalida(true);
    const spy = vi.fn();
    component.verRama.subscribe(spy);

    component.onVerRama();
    expect(spy).not.toHaveBeenCalled();
    expect(component.mostrarPr()).toBe(false);

    fixture.componentRef.setInput('origin', 'release/x');
    component.onVerRama();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(component.mostrarPr()).toBe(true);
  });

  it('destildar procesar salida vuelve a ocultar la columna PR', async () => {
    const { fixture, component } = await mount();
    fixture.componentRef.setInput('origin', 'release/x');
    component.onToggleProcesarSalida(true);
    component.onVerRama();
    expect(component.mostrarPr()).toBe(true);

    component.onToggleProcesarSalida(false);
    expect(component.mostrarPr()).toBe(false);
  });
});
