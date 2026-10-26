/**
 * Demo session: who is signed in (`personPhone`) and which cabinet/employee is active
 * (`companyId`, `role`). Kept in localStorage; falls back to memory when storage is blocked.
 */
const SESSION_KEY = "alfa-insights-session";

let memorySession = null;

export function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // Storage blocked or corrupted: use the in-memory copy.
  }
  return memorySession;
}

export function saveSession(session) {
  memorySession = { ...session };
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(memorySession));
  } catch {
    // Private mode: the session lives until page reload only.
  }
}

export function clearSession() {
  memorySession = null;
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // Nothing to clear.
  }
}
