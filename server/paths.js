import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const DATA_DIR = resolve(ROOT, 'data');
export const PUBLIC_DIR = resolve(ROOT, 'public');
export const MODULES_DIR = resolve(ROOT, 'node_modules');
