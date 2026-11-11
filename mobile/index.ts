import { registerRootComponent } from 'expo';

import './src/tracking'; // registers the background location task before the UI mounts
import App from './App';

registerRootComponent(App);
