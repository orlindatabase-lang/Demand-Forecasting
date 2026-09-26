/**
 * Tiny fetch wrapper for the FastAPI backend.
 *
 * Base URL: explicit VITE_API_URL wins if set, otherwise derived from
 * whatever host the page itself was loaded from (window.location.hostname)
 * with the API's port. This is what makes LAN sharing work without a
 * hardcoded IP: loading the dashboard via localhost:5173 calls the API at
 * localhost:8000; loading it via this machine's LAN IP (e.g.
 * 192.168.0.101:5173) calls the API at that same LAN IP:8000 — so it keeps
 * working automatically when the machine's IP changes (DHCP), instead of
 * silently hanging on a stale hardcoded address.
 */
export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  `http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:8000`;

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${res.status} ${res.statusText} — ${path}`);
  }
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`API ${res.status} ${res.statusText} — ${path}`);
  }
  return (await res.json()) as T;
}
