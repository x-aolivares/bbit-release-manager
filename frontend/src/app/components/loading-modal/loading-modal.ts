import { Component, input } from '@angular/core';
import { IonSpinner } from '@ionic/angular/ion-spinner';

@Component({
  selector: 'app-loading-modal',
  templateUrl: './loading-modal.html',
  styleUrl: './loading-modal.scss',
  standalone: true,
  imports: [IonSpinner],
})
export class LoadingModalComponent {
  open = input(false);
  finalizing = input(false);
}