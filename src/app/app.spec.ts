import { TestBed } from '@angular/core/testing';
import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
    })
      .compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the mockup components', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-top-bar')).toBeTruthy();
    expect(compiled.querySelector('app-repository-finder')).toBeTruthy();
    expect(compiled.querySelector('app-table-of-filtered-repositories')).toBeTruthy();
    expect(compiled.querySelector('app-repository-detail')).toBeTruthy();
    expect(compiled.querySelector('app-actions-modal')).toBeTruthy();
    expect(compiled.querySelector('app-create-pr-modal')).toBeTruthy();
  });
});
