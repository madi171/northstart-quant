const tokenKey = 'quant-token';

export function token() {
  const saved = localStorage.getItem(tokenKey);
  if (saved) return saved;
  const previous = sessionStorage.getItem(tokenKey);
  if (previous) {localStorage.setItem(tokenKey, previous); sessionStorage.removeItem(tokenKey);}
  return previous || '';
}

export function saveToken(value: string) {
  localStorage.setItem(tokenKey, value);
  sessionStorage.removeItem(tokenKey);
}

export function clearToken() {
  localStorage.removeItem(tokenKey);
  sessionStorage.removeItem(tokenKey);
}

export async function api<T = unknown>(path: string, body?: unknown, method?: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, {method: method || (body === undefined ? 'GET' : 'POST'), signal, headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token()}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') { clearToken(); window.dispatchEvent(new Event('quant-logout')); }
    throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  }
  return value as T;
}
export function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
