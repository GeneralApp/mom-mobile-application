import { useEffect, useState } from 'react';
import { Lock, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useAuth } from '../context/AuthContext.jsx';
import { resetPasswordWithToken } from '../services/auth.js';
import { Button } from '../components/common/Button.jsx';
import { Input } from '../components/common/Input.jsx';
import logo from '../assets/logo.png';

export function ResetPasswordPage({ onNavigateToLogin, onNavigateToForgotPassword }) {
  const { configured } = useAuth();

  const [ready, setReady] = useState(false);
  const [token, setToken] = useState('');
  const [checking, setChecking] = useState(true);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;

    // The emailed link looks like /reset-password?token=...
    const params = new URLSearchParams(window.location.search);
    const resetToken = params.get('token') || '';

    if (active) {
      if (resetToken) {
        setToken(resetToken);
        setReady(true);
      } else {
        setError(
          'This password reset link is invalid or has expired. Please request a new one.'
        );
      }
      setChecking(false);
    }

    return () => {
      active = false;
    };
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);

    try {
      await resetPasswordWithToken(token, password);
      setSuccess(true);

      // Drop the token from the address bar once it has been used.
      window.history.replaceState({}, '', '/reset-password');
    } catch (err) {
      setError(
        err?.message ||
          'Unable to update your password. Please request a new reset link.'
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center p-4 bg-slate-50">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <img
            src={logo}
            alt="Samvadini"
            className="w-20 h-20 object-contain mx-auto drop-shadow-sm rounded-2xl"
          />
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">
            Set a new password
          </h1>
          <p className="text-xs text-slate-500">
            Choose a new password for your Samvadini account.
          </p>
        </div>


        <div className="bg-white rounded-xl border border-slate-200 p-6 sm:p-8 shadow-xs">
          {checking ? (
            <div className="py-8 text-center text-sm text-slate-500">
              Validating your reset link...
            </div>
          ) : success ? (
            <div className="space-y-5 text-center">
              <div className="mx-auto w-12 h-12 rounded-full bg-emerald-50 flex items-center justify-center">
                <CheckCircle2 className="w-6 h-6 text-emerald-600" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-slate-900">
                  Password updated
                </h2>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  Your password has been changed successfully. You can now
                  sign in with your new password.
                </p>
              </div>
              <Button
                type="button"
                className="w-full"
                onClick={onNavigateToLogin}
              >
                Back to Sign In
              </Button>
            </div>
          ) : !ready ? (
            <div className="space-y-5 text-center">
              <div className="mx-auto w-12 h-12 rounded-full bg-amber-50 flex items-center justify-center">
                <AlertTriangle className="w-6 h-6 text-amber-600" />
              </div>
              <p className="text-sm leading-6 text-slate-500">
                {error}
              </p>
              <Button
                type="button"
                className="w-full"
                onClick={onNavigateToForgotPassword}
              >
                Request a New Reset Link
              </Button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <Input
                type="password"
                label="New Password"
                placeholder="At least 8 characters"
                icon={Lock}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoFocus
                minLength={8}
              />

              <Input
                type="password"
                label="Confirm New Password"
                placeholder="Enter the password again"
                icon={Lock}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />

              {error && (
                <p className="text-xs text-rose-600 bg-rose-50 p-2.5 rounded-lg border border-rose-200">
                  {error}
                </p>
              )}

              <Button
                type="submit"
                className="w-full"
                loading={loading}
                disabled={!configured}
              >
                Update Password
              </Button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

export default ResetPasswordPage;
