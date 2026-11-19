// Session storage for the backend-issued access token.
// The session is { access_token, token_type, expires_at, user }.

const SESSION_KEY = 'mom_session';

const listeners = new Set();

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getSession() {
  const raw = readStorage(SESSION_KEY);
  if (!raw) return null;

  try {
    const session = JSON.parse(raw);
    if (!session?.access_token) return null;

    if (session.expires_at && session.expires_at * 1000 <= Date.now()) {
      clearSession();
      return null;
    }

    return session;
  } catch {
    clearSession();
    return null;
  }
}

export function setSession(session) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // Storage unavailable (private mode); the session lives in memory only.
  }
  listeners.forEach((listener) => listener(session));
}

export function clearSession() {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore
  }
  listeners.forEach((listener) => listener(null));
}

export function getAccessToken() {
  // Demo sessions are stored separately (see AuthContext).
  try {
    const demoRaw = localStorage.getItem('mom_demo_session');
    if (demoRaw) {
      const parsed = JSON.parse(demoRaw);
      if (parsed?.access_token) return parsed.access_token;
    }
  } catch {
    // ignore
  }

  return getSession()?.access_token || null;
}

export function onSessionChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
