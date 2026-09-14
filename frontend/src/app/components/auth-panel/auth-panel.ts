import { Component, input, output } from '@angular/core';
import { FormsModule } from '@angular/forms';

export interface AuthRequest {
  alias: string;
  workspace: string;
  token: string;
  circleciToken: string;
}

@Component({
  selector: 'app-auth-panel',
  templateUrl: './auth-panel.html',
  styleUrl: './auth-panel.scss',
  standalone: true,
  imports: [FormsModule],
})
export class AuthPanelComponent {
  loading = input.required<boolean>();
  storedCreds = input.required<boolean>();

  connectRequested = output<AuthRequest>();
  reuseRequested = output<string>();
  useOtherToken = output<void>();

  protected alias = '';
  protected workspace = 'my_org_web_dev';
  protected token = '';
  protected circleciToken = '';
  protected circleciOpen = false;

  protected readonly bbTokenUrl =
    'https://id.atlassian.com/manage-profile/security/api-tokens';
  protected readonly cciTokenUrl =
    'https://app.circleci.com/settings/user/tokens';

  protected onConnect(): void {
    this.connectRequested.emit({
      alias: this.alias,
      workspace: this.workspace,
      token: this.token,
      circleciToken: this.circleciToken,
    });
  }

  protected onReuse(): void {
    this.reuseRequested.emit(this.alias);
  }

  protected showOtherToken(): void {
    this.useOtherToken.emit();
  }
}