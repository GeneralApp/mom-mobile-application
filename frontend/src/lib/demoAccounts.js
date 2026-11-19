/**
 * List of demo accounts: { name, email, password, id }.
 * These demo accounts sign in without a password check in the real project.
 * Keep in sync with backend/app/core/demo_accounts.py.
 */
export const DEMO_ACCOUNTS = [];

/**
 * Check if currently running in the local development environment (localhost / 127.0.0.1).
 */
export function isLocalEnvironment() {
  if (typeof window === 'undefined') return false;
  const hostname = window.location.hostname;
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '0.0.0.0';
}

/**
 * Find demo account by email (case-insensitive).
 */
export function findDemoAccount(email) {
  if (!email || typeof email !== 'string') return null;
  const cleanEmail = email.trim().toLowerCase();
  return DEMO_ACCOUNTS.find((a) => a.email.toLowerCase() === cleanEmail) || null;
}
