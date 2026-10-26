export type DataMode = 'historical' | 'demo' | 'live';
export const DATA_MODE_KEY = 'northstar-data-mode';

export function normalizeDataMode(value: unknown): DataMode {
  return value === 'demo' || value === 'live' ? value : 'historical';
}

export function readDataMode(): DataMode {
  try {
    return normalizeDataMode(globalThis.localStorage.getItem(DATA_MODE_KEY));
  } catch {
    return 'historical';
  }
}

export function saveDataMode(mode: string): void {
  try {
    globalThis.localStorage.setItem(DATA_MODE_KEY, normalizeDataMode(mode));
  } catch {
    return;
  }
}

export function researchDataMode(mode: string): 'historical' | 'demo' {
  return mode === 'demo' ? 'demo' : 'historical';
}
