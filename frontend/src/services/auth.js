import { apiRequest } from '../lib/api.js';
import { clearSession, setSession } from '../lib/session.js';

export async function getMe() {
  return await apiRequest('/me', { method: 'GET' });
}

export async function checkRoot() {
  return await apiRequest('/', { method: 'GET' });
}

export async function checkHealth() {
  return await apiRequest('/health', { method: 'GET' });
}

export async function checkDatabaseHealth() {
  return await apiRequest('/api/v1/health/db', { method: 'GET' });
}

// Returns { user, session } like the previous auth client did.
function toAuthResult(session) {
  setSession(session);
  return { user: session.user, session };
}

export async function signInWithPassword(email, password) {
  const session = await apiRequest('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  return toAuthResult(session);
}

export async function signUpWithPassword(email, password, name) {
  const session = await apiRequest('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({
      email,
      password,
      name: name || email.split('@')[0],
    }),
  });
  return toAuthResult(session);
}

export async function requestPasswordResetEmail(email) {
  return await apiRequest('/api/v1/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function resetPasswordWithToken(token, password) {
  return await apiRequest('/api/v1/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ token, password }),
  });
}

export async function changePassword(password) {
  const session = await apiRequest('/api/v1/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
  return toAuthResult(session);
}

export async function signOutUser() {
  clearSession();
}
