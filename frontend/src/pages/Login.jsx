import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Clock3, LoaderCircle, RefreshCw } from 'lucide-react';
import '../styles/loginVerification.css';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import AuthShell, { Field, inputClass } from '../components/auth/AuthShell';
import PasswordField from '../components/auth/PasswordField';
import OtpInput from '../components/auth/OtpInput';
import { PillButton, focusRing } from '../components/landing/ui';
import { useAuth } from '../context/AuthContext';

const CHALLENGE_SECONDS = 5 * 60;
const RESEND_SECONDS = 60;
const LOGIN_EXPIRED = 'Login session expired, please sign in again';

function formatClock(total) {
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { requestLogin, resendLoginCode, verifyLogin } = useAuth();
  const [email, setEmail] = useState(() => location.state?.email || '');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState(() =>
    location.state?.verificationPending ? 'code' : 'password',
  );
  const [errors, setErrors] = useState({});
  const [toast, setToast] = useState('');
  const [pendingAction, setPendingAction] = useState(null);
  const pending = pendingAction !== null;
  const requestPending = useRef(false);
  const [challengeLeft, setChallengeLeft] = useState(CHALLENGE_SECONDS);
  const [resendLeft, setResendLeft] = useState(RESEND_SECONDS);
  const resendDeadline = useRef(0);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(''), 5200);
    return () => clearTimeout(timer);
  }, [toast]);

  const returnToLogin = useCallback(
    (message) => {
      setStep('password');
      setCode('');
      setErrors({});
      setToast(message);
      navigate('/login', { replace: true, state: null });
    },
    [navigate],
  );

  useEffect(() => {
    if (step !== 'code') return undefined;
    const deadline = Date.now() + CHALLENGE_SECONDS * 1000;
    resendDeadline.current = Date.now() + RESEND_SECONDS * 1000;
    const timer = setInterval(() => {
      // Wall-clock deadlines stay accurate when a background tab throttles timers.
      setResendLeft(Math.max(0, Math.ceil((resendDeadline.current - Date.now()) / 1000)));
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setChallengeLeft(remaining);
      if (remaining === 0) {
        clearInterval(timer);
        returnToLogin(LOGIN_EXPIRED);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [step, returnToLogin]);

  async function onSubmit(event) {
    event.preventDefault();
    if (requestPending.current) return;
    const next = {};
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()))
      next.email = 'Enter a valid email address.';
    if (!password) next.password = 'Enter your password.';
    setErrors(next);
    if (Object.keys(next).length) return;
    requestPending.current = true;
    setPendingAction('login');
    setToast('');
    try {
      await requestLogin({ email: email.trim().toLowerCase(), password });
      setChallengeLeft(CHALLENGE_SECONDS);
      setResendLeft(RESEND_SECONDS);
      setStep('code');
      setToast('We sent a 6-digit code to your email.');
    } catch (error) {
      setToast(error.message || 'Invalid email or password');
    } finally {
      requestPending.current = false;
      setPendingAction(null);
    }
  }

  async function onVerify(event) {
    event.preventDefault();
    if (requestPending.current || challengeLeft === 0) return;
    const normalizedCode = code.trim();
    if (!/^\d{6}$/.test(normalizedCode)) {
      setErrors({ code: 'Enter the 6-digit email code.' });
      return;
    }
    requestPending.current = true;
    setPendingAction('verify');
    setToast('');
    try {
      const authenticatedUser = await verifyLogin({
        email: email.trim().toLowerCase(),
        code: normalizedCode,
      });
      const firstName = authenticatedUser.full_name?.trim().split(/\s+/, 1)[0] || 'there';
      const welcome = {
        title: `Welcome, ${firstName}`,
        message: 'Your secure workspace is ready for your next question.',
      };
      // Router state can be replaced by an auth refresh while navigation is in
      // flight. Keep this short-lived handoff in the tab as a reliable backup.
      try {
        window.sessionStorage.setItem('puchoo:welcome', JSON.stringify(welcome));
      } catch {
        // Router state below remains enough where storage is unavailable.
      }
      navigate('/ask', {
        replace: true,
        state: {
          welcome,
        },
      });
    } catch (error) {
      if (error.message === LOGIN_EXPIRED) {
        returnToLogin(error.message);
        return;
      }
      setToast(error.message || 'That code is invalid or has expired.');
    } finally {
      requestPending.current = false;
      setPendingAction(null);
    }
  }

  async function onResend() {
    if (requestPending.current || resendLeft > 0 || challengeLeft === 0) return;
    requestPending.current = true;
    setPendingAction('resend');
    setToast('');
    setErrors({});
    try {
      await resendLoginCode();
      setCode('');
      resendDeadline.current = Date.now() + RESEND_SECONDS * 1000;
      setResendLeft(RESEND_SECONDS);
      setToast('We sent a new 6-digit code to your email.');
    } catch (error) {
      if (error.message === LOGIN_EXPIRED) {
        returnToLogin(error.message);
        return;
      }
      setToast(error.message || 'Unable to resend the verification code.');
    } finally {
      requestPending.current = false;
      setPendingAction(null);
    }
  }

  return (
    <>
      <title>Log in · Puchoo.si</title>
      <AuthShell
        title={step === 'code' ? 'Check your email' : 'Log in'}
        subtitle={
          step === 'code'
            ? `Enter the 6-digit code sent to ${email.trim().toLowerCase()}.`
            : 'A verification code is emailed after your password is accepted.'
        }
        footer={
          <>
            New to Puchoo.si?{' '}
            <Link
              to="/signup"
              className={`text-[#111827] underline-offset-4 hover:underline ${focusRing} rounded-full`}
            >
              Get started
            </Link>
          </>
        }
      >
        {step === 'code' ? (
          <form className="space-y-5" onSubmit={onVerify} noValidate>
            <OtpInput
              id="code"
              value={code}
              onChange={setCode}
              error={errors.code}
              disabled={pending}
              autoFocus
            />
            <div className="login-verification-actions">
              <div className="login-verification-expiry">
                <Clock3 size={17} aria-hidden="true" />
                <span>Code expires in</span>
                <strong role="timer" aria-live="off">
                  {formatClock(challengeLeft)}
                </strong>
              </div>
              <button
                type="submit"
                className="login-verification-submit"
                disabled={pending || challengeLeft === 0}
              >
                {pendingAction === 'verify' ? 'Checking code…' : 'Verify and continue'}
                {pendingAction === 'verify' ? (
                  <LoaderCircle
                    size={18}
                    className="login-verification-spinner"
                    aria-hidden="true"
                  />
                ) : (
                  <ArrowRight size={18} aria-hidden="true" />
                )}
              </button>
              <div className="login-verification-resend-row">
                <span>Didn’t receive an email?</span>
                <button
                  type="button"
                  className="login-verification-resend"
                  disabled={pending || resendLeft > 0 || challengeLeft === 0}
                  onClick={onResend}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  {pendingAction === 'resend'
                    ? 'Sending code…'
                    : resendLeft > 0
                      ? `Resend code in ${resendLeft}s`
                      : 'Resend code'}
                </button>
              </div>
            </div>
            <button
              type="button"
              className="login-verification-back"
              disabled={pending}
              onClick={() => {
                setStep('password');
                setCode('');
                setErrors({});
                setToast('');
              }}
            >
              <ArrowLeft size={15} aria-hidden="true" /> Use a different email
            </button>
          </form>
        ) : null}
        {step === 'password' ? (
          <form className="space-y-5" onSubmit={onSubmit} noValidate>
            <Field id="email" label="Email" error={errors.email}>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                aria-invalid={Boolean(errors.email)}
                aria-describedby={errors.email ? 'email-error' : undefined}
                className={inputClass(Boolean(errors.email))}
              />
            </Field>
            <PasswordField
              id="password"
              label="Password"
              autoComplete="current-password"
              value={password}
              error={errors.password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <div className="-mt-2 text-right">
              <Link
                to="/forgot-password"
                className={`text-sm text-[#334d81] underline-offset-4 hover:underline ${focusRing} rounded-full`}
              >
                Forgot password?
              </Link>
            </div>
            <PillButton type="submit" variant="dark" disabled={pending}>
              {pending ? 'Logging in…' : 'Log in'}
            </PillButton>
          </form>
        ) : null}
      </AuthShell>
      {toast ? (
        <div
          role="alert"
          className="fixed right-4 bottom-4 z-50 max-w-sm rounded-2xl bg-[#111827] px-4 py-3 text-sm text-white"
        >
          {toast}
        </div>
      ) : null}
    </>
  );
}
