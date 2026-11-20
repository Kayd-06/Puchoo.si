import { StrictMode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import Signup from '../Signup';

const auth = vi.hoisted(() => ({
  requestSignup: vi.fn(),
}));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }));

function renderSignup() {
  return render(
    <StrictMode>
      <MemoryRouter>
        <Signup />
      </MemoryRouter>
    </StrictMode>,
  );
}

beforeEach(() => {
  auth.requestSignup.mockReset();
});

it('shows whether the passwords match and blocks submit until they do', () => {
  renderSignup();
  const password = screen.getByLabelText('Password');
  const confirm = screen.getByLabelText('Confirm password');
  const submit = screen.getByRole('button', { name: 'Get started' });

  fireEvent.change(password, { target: { value: 'StrongPassword1' } });
  expect(screen.getByText('Password strength: Strong')).toBeInTheDocument();
  expect(screen.queryByText('Passwords match')).not.toBeInTheDocument();
  expect(submit).toBeEnabled();

  fireEvent.change(confirm, { target: { value: 'DifferentPass1' } });
  expect(screen.getByText("Passwords don't match")).toBeInTheDocument();
  expect(confirm).toHaveAttribute('aria-invalid', 'true');
  expect(submit).toBeDisabled();

  fireEvent.submit(confirm.closest('form'));
  expect(auth.requestSignup).not.toHaveBeenCalled();

  fireEvent.change(confirm, { target: { value: 'StrongPassword1' } });
  expect(screen.getByText('Passwords match')).toBeInTheDocument();
  expect(confirm).toHaveAttribute('aria-invalid', 'false');
  expect(submit).toBeEnabled();
});
