import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RepositoryFinder } from './repository-finder';

describe('RepositoryFinder', () => {
  let component: RepositoryFinder;
  let fixture: ComponentFixture<RepositoryFinder>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RepositoryFinder],
    }).compileComponents();

    fixture = TestBed.createComponent(RepositoryFinder);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
