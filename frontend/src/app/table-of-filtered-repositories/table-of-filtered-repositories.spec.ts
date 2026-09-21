import { ComponentFixture, TestBed } from '@angular/core/testing';
import { TableOfFilteredRepositories } from './table-of-filtered-repositories';

describe('TableOfFilteredRepositories', () => {
  let component: TableOfFilteredRepositories;
  let fixture: ComponentFixture<TableOfFilteredRepositories>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TableOfFilteredRepositories],
    }).compileComponents();

    fixture = TestBed.createComponent(TableOfFilteredRepositories);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
