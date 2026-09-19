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

  it('repoUrl sin procesar salida apunta a la rama default (workspace + default_branch)', () => {
    const component = TestBed.createComponent(ReposBuscadosComponent).componentInstance;
    const row: ReposBuscarRow = {
      slug: 'bbit-trnxd-01',
      name: '',
      workspace: 'my_org_web_dev',
      default_branch: 'master',
      tags: [],
      branch_url: 'https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/release/REP-325073',
    };
    expect(component.repoUrl(row)).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/master');
  });

  it('repoUrl con procesar salida + ver rama apunta a la rama origen', async () => {
    const { fixture, component } = await mount();
    fixture.componentRef.setInput('origin', 'release/REP-325073');
    component.onToggleProcesarSalida(true);
    component.onVerRama();

    const row: ReposBuscarRow = {
      slug: 'bbit-trnxd-01',
      name: '',
      workspace: 'my_org_web_dev',
      default_branch: 'master',
      tags: [],
      branch_url: 'https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/release/REP-325073',
    };
    expect(component.mostrarPr()).toBe(true);
    expect(component.repoUrl(row)).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/release/REP-325073');
  });

  it('repoUrl sin workspace/default_branch cae al root del repo', () => {
    const component = TestBed.createComponent(ReposBuscadosComponent).componentInstance;
    expect(
      component.repoUrl({ slug: 'r1', name: 'R1', workspace: 'ws', default_branch: '', tags: [], branch_url: 'https://bitbucket.org/ws/r1/src/release/x' }),
    ).toBe('https://bitbucket.org/ws/r1');
    expect(
      component.repoUrl({ slug: 'r1', name: 'R1', workspace: '', default_branch: '', tags: [] }),
    ).toBe('');
  });

  it('el slug se renderiza como link a la rama default cuando no hay procesar salida', async () => {
    const { fixture } = await mount();
    fixture.componentRef.setInput('repos', [
      { slug: 'bbit-trnxd-01', name: '', workspace: 'my_org_web_dev', default_branch: 'main', tags: [], branch_url: 'https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/release/x' },
    ]);
    fixture.detectChanges();

    const anchor = fixture.nativeElement.querySelector('a.bb-link');
    expect(anchor).toBeTruthy();
    expect(anchor.getAttribute('href')).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/main');
    expect(anchor.getAttribute('target')).toBe('_blank');
  });

  it('sin cambios no ofrece crear PR y muestra el estado muted', async () => {
    const { fixture, component } = await mount();
    fixture.componentRef.setInput('origin', 'release/x');
    fixture.componentRef.setInput('states', { r1: 'found' });
    fixture.componentRef.setInput('repos', [
      { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', tags: [], no_changes: true },
    ]);
    component.onToggleProcesarSalida(true);
    component.onVerRama();
    fixture.detectChanges();

    const row = { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', tags: [], no_changes: true };
    const el = fixture.nativeElement as HTMLElement;
    expect(component.sinCambios(row)).toBe(true);
    expect(component.puedeCrearPr(row)).toBe(false);
    const btn = Array.from(el.querySelectorAll('button') as unknown as HTMLButtonElement[])
      .find((b) => b.textContent?.trim() === 'Crear PR');
    expect(btn).toBeUndefined();
    expect(el.textContent).toContain('Sin cambios');
  });

  it('con rama resuelta y cambios ofrece crear PR', async () => {
    const { fixture, component } = await mount();
    fixture.componentRef.setInput('origin', 'release/x');
    fixture.componentRef.setInput('states', { r1: 'found' });
    fixture.componentRef.setInput('repos', [
      { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', tags: [], no_changes: false },
    ]);
    component.onToggleProcesarSalida(true);
    component.onVerRama();
    fixture.detectChanges();

    const row = { slug: 'r1', name: 'R1', workspace: 'ws', default_branch: 'master', tags: [], no_changes: false };
    expect(component.sinCambios(row)).toBe(false);
    expect(component.puedeCrearPr(row)).toBe(true);
    const el2 = fixture.nativeElement as HTMLElement;
    const btn = Array.from(el2.querySelectorAll('button') as unknown as HTMLButtonElement[])
      .find((b) => b.textContent?.trim() === 'Crear PR');
    expect(btn).toBeTruthy();
  });
});
