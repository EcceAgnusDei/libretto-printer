export type StoredSession = {
  sessionId: string;
  sourceName: string;
  pdfPageCount: number;
  updatedAt: string;
};

const STORAGE_KEY = "libretto-printer:sessions";
const MAX_SESSIONS = 10;
export const SESSION_ID_RE = /^[a-f0-9]{32}$/;

function canUseStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function isValidEntry(value: unknown): value is StoredSession {
  if (!value || typeof value !== "object") return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.sessionId === "string" &&
    SESSION_ID_RE.test(entry.sessionId) &&
    typeof entry.sourceName === "string" &&
    typeof entry.pdfPageCount === "number" &&
    typeof entry.updatedAt === "string"
  );
}

export function loadStoredSessions(): StoredSession[] {
  if (!canUseStorage()) return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isValidEntry).slice(0, MAX_SESSIONS);
  } catch {
    return [];
  }
}

export function saveStoredSessions(entries: StoredSession[]): void {
  if (!canUseStorage()) return;
  const cleaned = entries.filter(isValidEntry).slice(0, MAX_SESSIONS);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cleaned));
}

export function upsertStoredSession(entry: StoredSession): StoredSession[] {
  const next = [
    entry,
    ...loadStoredSessions().filter((item) => item.sessionId !== entry.sessionId),
  ].slice(0, MAX_SESSIONS);
  saveStoredSessions(next);
  return next;
}

export function removeStoredSession(sessionId: string): StoredSession[] {
  const next = loadStoredSessions().filter((item) => item.sessionId !== sessionId);
  saveStoredSessions(next);
  return next;
}

export function parseSessionIdFromLocation(
  search: string,
  hash: string,
): string | null {
  const hashBody = hash.startsWith("#") ? hash.slice(1) : hash;
  const fromHash = new URLSearchParams(hashBody).get("session");
  if (fromHash && SESSION_ID_RE.test(fromHash)) return fromHash;

  const query = search.startsWith("?") ? search.slice(1) : search;
  const fromQuery = new URLSearchParams(query).get("session");
  if (fromQuery && SESSION_ID_RE.test(fromQuery)) return fromQuery;
  return null;
}

export function buildHrefWithSession(
  pathname: string,
  search: string,
  sessionId: string | null,
): string {
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  params.delete("session");
  const query = params.toString();
  const pathWithQuery = query ? `${pathname}?${query}` : pathname;
  if (sessionId && SESSION_ID_RE.test(sessionId)) {
    return `${pathWithQuery}#session=${sessionId}`;
  }
  return pathWithQuery;
}

export function readSessionIdFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  const id = parseSessionIdFromLocation(
    window.location.search,
    window.location.hash,
  );
  if (!id) return null;
  const fromQuery = new URLSearchParams(window.location.search).get("session");
  if (fromQuery && SESSION_ID_RE.test(fromQuery)) {
    writeSessionIdToUrl(id);
  }
  return id;
}

export function writeSessionIdToUrl(sessionId: string | null): void {
  if (typeof window === "undefined") return;
  const next = buildHrefWithSession(
    window.location.pathname,
    window.location.search,
    sessionId,
  );
  window.history.replaceState(null, "", next);
}

export function shouldApplyRestoredFocus(
  userStartedDuringRestore: boolean,
): boolean {
  return !userStartedDuringRestore;
}

export function mergeTrackedById<T extends { sessionId: string }>(
  restored: T[],
  current: T[],
): T[] {
  const byId = new Map<string, T>();
  for (const item of restored) {
    byId.set(item.sessionId, item);
  }
  for (const item of current) {
    byId.set(item.sessionId, item);
  }
  return [...byId.values()];
}
