import { mergeConfig } from 'vite';
import config from './vite.config.mjs';

// Production builds do not contain HMR. Browser art CI needs static production
// primitives, not a dev-only websocket that Chrome may block on loopback.
export default mergeConfig(config, { server: { hmr: false } });
