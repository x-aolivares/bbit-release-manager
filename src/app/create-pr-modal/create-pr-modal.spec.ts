import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CreatePrModal } from './create-pr-modal';

describe('CreatePrModal', () => {
  let component: CreatePrModal;
  let fixture: ComponentFixture<CreatePrModal>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CreatePrModal],
    }).compileComponents();

    fixture = TestBed.createComponent(CreatePrModal);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
