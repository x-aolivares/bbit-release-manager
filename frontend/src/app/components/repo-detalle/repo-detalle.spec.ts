import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';
import { DetalleRepo, RepoDetalleComponent } from './repo-detalle';

function repo(overrides: Partial<DetalleRepo> = {}): DetalleRepo {
  return {
    slug: 'bbit-trnxd-01',
    workspace: 'my_org_web_dev',
    resolved_branch: 'release/REP-325073',
    commit: '9ccf8628091cabcdef1234567890',
    ci_project: 'bb/my_org_web_dev/bbit-trnxd-01',
    ci_vcs: 'bb',
    pr: null,
    tags: {},
    params: [],
    ...overrides,
  };
}

describe('RepoDetalleComponent — enlaces importantes', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  it('arma los enlaces de Bitbucket y CircleCI cuando hay datos', async () => {
    const fixture = TestBed.createComponent(RepoDetalleComponent);
    fixture.componentRef.setInput('repo', repo());

    const enlaces = fixture.componentInstance.enlaces();
    expect(enlaces.map((e) => e.label)).toEqual([
      'Bitbucket — repo',
      'Bitbucket — rama release/REP-325073',
      'Bitbucket — commit 9ccf8628091c',
      'CircleCI — pipelines',
      'CircleCI — rama release/REP-325073',
    ]);
    expect(enlaces[0].url).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/browse');
    expect(enlaces[1].url).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/branch/release%2FREP-325073');
    expect(enlaces[2].url).toBe('https://bitbucket.org/my_org_web_dev/bbit-trnxd-01/commits/9ccf8628091cabcdef1234567890');
    expect(enlaces[3].url).toBe('https://app.circleci.com/pipelines/bb/my_org_web_dev?useNewPipelines=true&project=bb/my_org_web_dev/bbit-trnxd-01');
    expect(enlaces[4].url).toBe('https://app.circleci.com/pipelines/bb/my_org_web_dev?useNewPipelines=true&project=bb/my_org_web_dev/bbit-trnxd-01&branch=release%2FREP-325073');
  });

  it('omite los enlaces de rama/commit/CircleCI sin datos', async () => {
    const fixture = TestBed.createComponent(RepoDetalleComponent);
    fixture.componentRef.setInput('repo', repo({ resolved_branch: '', commit: '', ci_project: null }));

    const enlaces = fixture.componentInstance.enlaces();
    expect(enlaces.length).toBe(1);
    expect(enlaces[0].label).toBe('Bitbucket — repo');
  });
});