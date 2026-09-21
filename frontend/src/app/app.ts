import { Component, inject } from '@angular/core';
import { TopBar } from './top-bar/top-bar';
import { RepositoryFinder } from './repository-finder/repository-finder';
import { TableOfFilteredRepositories } from './table-of-filtered-repositories/table-of-filtered-repositories';
import { RepositoryDetail } from './repository-detail/repository-detail';
import { ActionsModal } from './actions-modal/actions-modal';
import { CreatePrModal } from './create-pr-modal/create-pr-modal';
import { ReleaseStore } from './shared/release-store';

@Component({
  imports: [RepositoryFinder, TopBar, TableOfFilteredRepositories, RepositoryDetail, ActionsModal, CreatePrModal],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  readonly store = inject(ReleaseStore);
}