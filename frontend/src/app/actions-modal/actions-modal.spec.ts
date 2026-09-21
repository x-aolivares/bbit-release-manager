import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActionsModal } from './actions-modal';

describe('ActionsModal', () => {
  let component: ActionsModal;
  let fixture: ComponentFixture<ActionsModal>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActionsModal],
    }).compileComponents();

    fixture = TestBed.createComponent(ActionsModal);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
