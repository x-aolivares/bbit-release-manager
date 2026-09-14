import {
  Component,
  EventEmitter,
  Input,
  Output,
  output,
  WritableSignal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Panel "Resolver ramas": inputs de rama origen/destino, fuerza de caché y
 * los chips de blacklist, prefijos de proyecto y ambientes de deploy.
 *
 * El estado vive en Home (source of truth); este componente recibe los
 * WritableSignal para editarse en el template y emite eventos para las
 * acciones (agregar/remover chips, consultar).
 */
@Component({
  selector: 'app-query-panel',
  templateUrl: './query-panel.html',
  styleUrl: './query-panel.scss',
  standalone: true,
  imports: [FormsModule],
})
export class QueryPanelComponent {
  @Input() origin = '';
  @Output() originChange = new EventEmitter<string>();
  @Input() destination = '';
  @Output() destinationChange = new EventEmitter<string>();

  @Input() forceCache!: WritableSignal<boolean>;

  @Input() prefixes!: WritableSignal<string[]>;
  @Input() addingPrefix!: WritableSignal<boolean>;
  @Input() prefixInput = '';
  @Output() prefixInputChange = new EventEmitter<string>();

  @Input() blacklisted!: WritableSignal<string[]>;
  @Input() addingBlacklist!: WritableSignal<boolean>;
  @Input() blacklistInput = '';
  @Output() blacklistInputChange = new EventEmitter<string>();

  @Input() projectPrefixes!: WritableSignal<string[]>;
  @Input() addingProjectPrefix!: WritableSignal<boolean>;
  @Input() projectPrefixInput = '';
  @Output() projectPrefixInputChange = new EventEmitter<string>();

  @Input() reposLoading!: WritableSignal<boolean>;

  loadRepos = output<void>();
  addPrefix = output<void>();
  removePrefix = output<number>();
  addBlacklist = output<void>();
  removeBlacklist = output<number>();
  addProjectPrefix = output<void>();
  removeProjectPrefix = output<number>();
}