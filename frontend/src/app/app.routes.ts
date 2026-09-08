import { Routes } from '@angular/router';
import { Home } from './pages/home/home';
import { SsmView } from './pages/ssm-view/ssm-view';

export const routes: Routes = [
  { path: '', component: Home },
  { path: 'ssm/:param', component: SsmView },
  { path: '**', redirectTo: '' },
];