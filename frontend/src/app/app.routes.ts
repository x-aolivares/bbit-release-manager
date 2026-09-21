import { Routes } from '@angular/router';
import { Home } from './pages/home/home';
import { SsmView } from './pages/ssm-view/ssm-view';
import { ReposRediseno } from './pages/repos-rediseno/repos-rediseno';

export const routes: Routes = [
  { path: '', component: Home },
  { path: 'rediseno', component: ReposRediseno },
  { path: 'ssm/:param', component: SsmView },
  { path: '**', redirectTo: '' },
];