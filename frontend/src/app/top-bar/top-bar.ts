import { Component, OnInit } from '@angular/core';
import { APP_CONFIG } from '../shared/app-config';

@Component({
  imports: [],
  selector: 'app-top-bar',
  styleUrl: './top-bar.css',
  templateUrl: './top-bar.html',
})
export class TopBar implements OnInit {
  workspace = APP_CONFIG.workspace;
  totalRepos: number = 0;

  ngOnInit(): void {
    this.totalRepos = APP_CONFIG.total_repos;
  }
}