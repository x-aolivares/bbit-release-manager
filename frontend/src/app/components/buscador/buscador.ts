import { Component, EventEmitter, Input, Output, output, WritableSignal } from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Buscador universal de repositorios: prefijos de proyecto + blacklist
 * (los únicos filtros). "Buscar repositorios" consulta todos los repos del
 * workspace y la tabla pinta SOLO los que coinciden con los filtros.
 *
 * El estado vive en Home; este componente recibe los WritableSignal para
 * editarse en el template y emite eventos para las acciones (agregar/remover
 * chips y consultar).
 */
@Component({
  selector: 'app-buscador',
  templateUrl: './buscador.html',
  styleUrl: './buscador.scss',
  standalone: true,
  imports: [FormsModule],
})
export class BuscadorComponent {
  @Input() projectPrefixes!: WritableSignal<string[]>;
  @Input() addingProjectPrefix!: WritableSignal<boolean>;
  @Input() projectPrefixInput = '';
  @Output() projectPrefixInputChange = new EventEmitter<string>();

  @Input() blacklisted!: WritableSignal<string[]>;
  @Input() addingBlacklist!: WritableSignal<boolean>;
  @Input() blacklistInput = '';
  @Output() blacklistInputChange = new EventEmitter<string>();

  @Input() reposLoading!: WritableSignal<boolean>;

  buscarRepos = output<void>();
  addProjectPrefix = output<void>();
  removeProjectPrefix = output<number>();
  addBlacklist = output<void>();
  removeBlacklist = output<number>();
}