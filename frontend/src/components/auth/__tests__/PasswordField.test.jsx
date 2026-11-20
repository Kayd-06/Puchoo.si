import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import PasswordField from '../PasswordField';
import { useState } from 'react';

function ConfirmHarness() {
  const [password, setPassword] = useState('StrongPassword1');
  const [confirm, setConfirm] = useState('');
  return (
    <>
      <PasswordField
        id="password"
        label="Password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <PasswordField
        id="confirm_password"
        label="Confirm password"
        value={confirm}
        showStrength={false}
        matchValue={password}
        onChange={(event) => setConfirm(event.target.value)}
      />
    </>
  );
}

it('shows strength only on the password field and a live match message on confirm', () => {
  render(<ConfirmHarness />);
  const confirm = screen.getByLabelText('Confirm password');

  expect(screen.getByText('Password strength: Strong')).toBeInTheDocument();
  expect(screen.getAllByText(/Password strength:|Use at least 10 characters/)).toHaveLength(1);
  expect(screen.queryByText('Passwords match')).not.toBeInTheDocument();
  expect(screen.queryByText("Passwords don't match")).not.toBeInTheDocument();
  expect(confirm).toHaveAttribute('aria-invalid', 'false');

  fireEvent.change(confirm, { target: { value: 'DifferentPass1' } });
  const mismatch = screen.getByText("Passwords don't match");
  expect(mismatch).toHaveAttribute('aria-live', 'polite');
  expect(confirm).toHaveAttribute('aria-invalid', 'true');
  expect(confirm).toHaveAttribute('aria-describedby', 'confirm_password-match');

  fireEvent.change(confirm, { target: { value: 'StrongPassword1' } });
  expect(screen.getByText('Passwords match')).toHaveAttribute('aria-live', 'polite');
  expect(confirm).toHaveAttribute('aria-invalid', 'false');
  expect(screen.queryByText("Passwords don't match")).not.toBeInTheDocument();
});
