import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RepositoryDetail } from './repository-detail';

describe('RepositoryDetail', () => {
  let component: RepositoryDetail;
  let fixture: ComponentFixture<RepositoryDetail>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RepositoryDetail],
    }).compileComponents();

    fixture = TestBed.createComponent(RepositoryDetail);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
