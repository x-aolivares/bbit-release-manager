import { Component, signal } from '@angular/core';
import { TopBar } from './top-bar/top-bar';
import { RepositoryFinder } from './repository-finder/repository-finder';
import { TableOfFilteredRepositories } from './table-of-filtered-repositories/table-of-filtered-repositories';

@Component({
  imports: [RepositoryFinder, TopBar, TableOfFilteredRepositories],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly title = signal('bbit-release-manager');
}

