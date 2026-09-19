import { bootstrapApplication } from '@angular/platform-browser';
import { addIcons } from 'ionicons';
import {
  arrowBackOutline,
  chevronDownOutline,
  logOutOutline,
  settingsOutline,
  timeOutline,
  trashOutline,
} from 'ionicons/icons';
import { appConfig } from './app/app.config';
import { App } from './app/app';

addIcons({
  'arrow-back-outline': arrowBackOutline,
  'chevron-down-outline': chevronDownOutline,
  'log-out-outline': logOutOutline,
  'settings-outline': settingsOutline,
  'time-outline': timeOutline,
  'trash-outline': trashOutline,
});

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
