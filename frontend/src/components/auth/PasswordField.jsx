import { useState } from 'react';
import { Check, Eye, EyeOff } from 'lucide-react';
import { focusRing } from '../landing/ui';
import { Field, inputClass } from './AuthShell';

export function passwordIsValid(password) {
  return (
    password.length >= 10 &&
    password.length <= 128 &&
    /[A-Za-z]/.test(password) &&
    /\d/.test(password)
  );
}

export function strengthOf(password) {
  if (!password) return { score: 0, label: '' };
  const checks = [
    password.length >= 10,
    /[A-Za-z]/.test(password),
    /\d/.test(password),
    password.length >= 14 || /[^A-Za-z0-9]/.test(password),
  ];
  const score = checks.filter(Boolean).length;
  return { score, label: ['Too short', 'Weak', 'Fair', 'Good', 'Strong'][score] };
}

export default function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  autoComplete,
  className = '',
  showStrength = true,
  matchValue,
}) {
  const [visible, setVisible] = useState(false);
  const strength = strengthOf(value);
  const confirmStarted = matchValue !== undefined && value.length > 0;
  const passwordsMatch = confirmStarted && value === matchValue;
  const passwordsMismatch = confirmStarted && value !== matchValue;
  const invalid = Boolean(error) || passwordsMismatch;
  const describedBy = [
    error ? `${id}-error` : null,
    confirmStarted ? `${id}-match` : null,
    showStrength ? `${id}-strength` : null,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <Field id={id} label={label} error={error} className={className}>
      <div className="relative">
        <input
          id={id}
          name={id}
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          value={value}
          onChange={onChange}
          aria-invalid={invalid}
          aria-describedby={describedBy || undefined}
          className={`${inputClass(invalid)} pr-12`}
        />
        <button
          type="button"
          className={`absolute top-1/2 right-3 -translate-y-1/2 rounded-full p-1 text-[#6B7280] ${focusRing}`}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? (
            <EyeOff className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Eye className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </div>
      {showStrength ? (
        <div className="mt-2" id={`${id}-strength`}>
          <div className="flex gap-1" aria-hidden="true">
            {[0, 1, 2, 3].map((segment) => (
              <span
                key={segment}
                className={`h-1 flex-1 rounded-full ${strength.score > segment ? 'bg-[#111827]' : 'bg-black/10'}`}
              />
            ))}
          </div>
          <p className="mt-1 text-xs text-[#6B7280]" aria-live="polite">
            {strength.label
              ? `Password strength: ${strength.label}`
              : 'Use at least 10 characters, with a letter and a number.'}
          </p>
        </div>
      ) : null}
      {confirmStarted ? (
        <p
          id={`${id}-match`}
          role="status"
          aria-live="polite"
          className={`mt-2 flex items-center gap-1.5 text-xs font-medium ${
            passwordsMatch ? 'text-[#166534]' : 'text-[#991B1B]'
          }`}
        >
          {passwordsMatch ? (
            <>
              <Check className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Passwords match
            </>
          ) : (
            "Passwords don't match"
          )}
        </p>
      ) : null}
    </Field>
  );
}
