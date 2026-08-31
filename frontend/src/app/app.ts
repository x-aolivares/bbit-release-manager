import { Component } from '@angular/core';
import { IonApp } from '@ionic/angular/ion-app';
import { IonRouterOutlet } from '@ionic/angular/ion-router-outlet';

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.scss',
  standalone: true,
  imports: [IonApp, IonRouterOutlet],
})
export class App {
  constructor() {}
}