import { StrictMode } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Login from '../Login';

const auth = vi.hoisted(() => ({
  requestLogin: vi.fn(),
  resendLoginCode: vi.fn(),
  verifyLogin: vi.fn(),
}));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }));

function renderCode() {
  return render(
    <StrictMode>
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/login',
            state: { verificationPending: true, email: 'member@example.com' },
          },
        ]}
      >
        <Login />
      </MemoryRouter>
    </StrictMode>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('keeps resend disabled until the cooldown ends and identifies the pending action', async () => {
  let rejectResend;
  auth.resendLoginCode.mockImplementation(
    () =>
      new Promise((_, reject) => {
        rejectResend = reject;
      }),
  );
  renderCode();
  expect(screen.getByRole('button', { name: /Resend code in 60s/ })).toBeDisabled();
  await act(async () => vi.advanceTimersByTime(60000));
  fireEvent.click(screen.getByRole('button', { name: 'Resend code' }));
  expect(screen.getByRole('button', { name: /Sending code/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Verify and continue' })).toBeDisabled();
  await act(async () => rejectResend(new Error('Email delivery unavailable')));
  expect(screen.getByRole('alert')).toHaveTextContent('Email delivery unavailable');
  expect(screen.getByRole('button', { name: 'Resend code' })).toBeEnabled();
});

it('shows invalid-code errors and recovers from a failed verification without crashing', async () => {
  auth.verifyLogin.mockRejectedValue(new Error('That code is invalid or has expired.'));
  renderCode();
  fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Enter the 6-digit email code.');
  expect(auth.verifyLogin).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Verification code'), { target: { value: '123456' } });
  await act(async () =>
    fireEvent.submit(screen.getByLabelText('Verification code').closest('form')),
  );
  expect(screen.getByText('That code is invalid or has expired.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Verify and continue' })).toBeEnabled();
});

it('expires in StrictMode without updating the router during render', async () => {
  const error = vi.spyOn(console, 'error').mockImplementation(() => {});
  renderCode();
  await act(async () => vi.advanceTimersByTime(300000));
  expect(screen.getByLabelText('Email')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Login session expired');
  expect(error.mock.calls.flat().join(' ')).not.toMatch(
    /while rendering|Cannot update a component/,
  );
});

it('sends only one verification request for repeated submits while pending', async () => {
  let resolveVerify;
  auth.verifyLogin.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveVerify = resolve;
      }),
  );
  renderCode();
  const input = screen.getByLabelText('Verification code');
  fireEvent.change(input, { target: { value: '123456' } });
  fireEvent.submit(input.closest('form'));
  fireEvent.submit(input.closest('form'));
  expect(auth.verifyLogin).toHaveBeenCalledTimes(1);
  await act(async () => resolveVerify({ full_name: 'Member' }));
});

it('clears the old code after resend and preserves the original challenge expiry', async () => {
  auth.resendLoginCode.mockResolvedValue({});
  renderCode();
  fireEvent.change(screen.getByLabelText('Verification code'), { target: { value: '123456' } });
  await act(async () => vi.advanceTimersByTime(60000));
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Resend code' })));
  expect(screen.getByLabelText('Verification code')).toHaveValue('');
  expect(screen.getByRole('timer')).toHaveTextContent('4:00');
  expect(screen.getByRole('button', { name: 'Resend code in 60s' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('We sent a new 6-digit code');
});

it('returns to login when verification reports an expired server session', async () => {
  auth.verifyLogin.mockRejectedValue(new Error('Login session expired, please sign in again'));
  renderCode();
  fireEvent.change(screen.getByLabelText('Verification code'), { target: { value: '123456' } });
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Verify and continue' })),
  );
  expect(screen.getByLabelText('Email')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Log in' })).toBeEnabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Login session expired');
});
