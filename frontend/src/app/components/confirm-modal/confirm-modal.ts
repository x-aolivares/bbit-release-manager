import { Component, input, output } from '@angular/core';

@Component({
  selector: 'app-confirm-modal',
  templateUrl: './confirm-modal.html',
  styleUrl: './confirm-modal.scss',
  standalone: true,
})
export class ConfirmModalComponent {
  open = input(false);

  confirmDelete = output<void>();
  confirmKeep = output<void>();
  cancel = output<void>();
}