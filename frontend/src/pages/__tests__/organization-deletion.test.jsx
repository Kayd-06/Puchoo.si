import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import Settings from '../Settings';
let role;
let workspaceType;
let source;
const refreshWorkspaces = vi.fn();
const fetchApi = vi.fn();
const deleteOrganization = vi.fn();
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { role, workspace_type: workspaceType, workspace_name: 'Northstar', capabilities: {} },
    deleteOrganization,
  }),
}));
vi.mock('../../context/AppContext', () => ({
  useAppContext: () => ({ activeWorkspace: source, refreshWorkspaces, removeWorkspace: vi.fn() }),
}));
vi.mock('../../api/client', () => ({ fetchApi: (...args) => fetchApi(...args) }));
vi.mock('../../components/auth/ChangeEmailPanel', () => ({ default: () => null }));
beforeEach(() => {
  role = 'owner';
  workspaceType = 'business';
  source = { id: 'school-source', name: 'School1', source_type: 'spreadsheet' };
  fetchApi.mockReset();
  refreshWorkspaces.mockReset();
  deleteOrganization.mockReset();
});
it.each(['admin', 'editor', 'viewer'])('hides organization deletion from %s', (nextRole) => {
  role = nextRole;
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  expect(screen.queryByRole('button', { name: 'Delete business' })).not.toBeInTheDocument();
});
it('requires the exact name and states that member accounts will be deleted', async () => {
  deleteOrganization.mockResolvedValue(undefined);
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Organization controls' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete business' }));
  expect(screen.getByText(/all member accounts/i)).toBeInTheDocument();
  const confirm = screen.getByRole('button', { name: 'Permanently delete business' });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Type Northstar to confirm'), {
    target: { value: 'Wrong' },
  });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Type Northstar to confirm'), {
    target: { value: 'Northstar' },
  });
  // The exact name alone is not enough: the owner must re-enter their password.
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Your current password'), {
    target: { value: 'owner-password' },
  });
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  await waitFor(() =>
    expect(deleteOrganization).toHaveBeenCalledWith('Northstar', 'owner-password'),
  );
});

it('keeps the organization and clears the password when the server rejects it', async () => {
  deleteOrganization.mockRejectedValue(new Error('The password or name you entered is incorrect.'));
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Organization controls' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete business' }));
  fireEvent.change(screen.getByLabelText('Type Northstar to confirm'), {
    target: { value: 'Northstar' },
  });
  const password = screen.getByLabelText('Your current password');
  expect(password).toHaveAttribute('type', 'password');
  fireEvent.change(password, { target: { value: 'wrong-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Permanently delete business' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('password or name');
  expect(password).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Permanently delete business' })).toBeDisabled();
});

it.each(['institution', 'institute'])('uses the school category for %s accounts', (type) => {
  workspaceType = type;
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Organization controls' }));
  expect(screen.getByRole('button', { name: 'Delete school' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Delete business' })).not.toBeInTheDocument();
});
it('names the selected source and deletes it without deleting the organization', async () => {
  fetchApi.mockResolvedValue(null);
  refreshWorkspaces.mockResolvedValue([]);
  render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Delete data source' }));
  fireEvent.change(screen.getByLabelText('Type School1 to confirm'), {
    target: { value: 'School1' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Permanently delete data source' }));
  await waitFor(() =>
    expect(fetchApi).toHaveBeenCalledWith('/workspaces/school-source', { method: 'DELETE' }),
  );
  expect(deleteOrganization).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('School1 was deleted'));
});

it('collapses after a source delete and shows only the outcome until dismissed', async () => {
  fetchApi.mockResolvedValue(null);
  const { rerender } = render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Delete data source' }));
  fireEvent.change(screen.getByLabelText('Type School1 to confirm'), {
    target: { value: 'School1' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Permanently delete data source' }));
  await screen.findByText(/School1 was deleted/);
  // The context selects the next source; its delete controls must not appear on their own.
  source = { id: 'reports', name: 'Reports', source_type: 'spreadsheet' };
  rerender(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  expect(screen.queryByRole('heading', { name: 'Delete Reports' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/to confirm/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Done' }));
  expect(screen.queryByText(/School1 was deleted/)).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Delete Reports' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete data source' })).toBeInTheDocument();
  expect(screen.queryByLabelText('Type Reports to confirm')).not.toBeInTheDocument();
});

it('shows only selected-source deletion initially and resets confirmation after switching sources', async () => {
  fetchApi.mockResolvedValue(null);
  const { rerender } = render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  expect(screen.getByRole('heading', { name: 'Delete School1' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Delete business' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Delete data source' }));
  fireEvent.change(screen.getByLabelText('Type School1 to confirm'), {
    target: { value: 'School1' },
  });
  source = { id: 'business-source', name: 'Business', source_type: 'spreadsheet' };
  rerender(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  expect(screen.getByRole('heading', { name: 'Delete Business' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Delete School1' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Type School1 to confirm')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Permanently delete data source' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Delete data source' }));
  const confirm = screen.getByRole('button', { name: 'Permanently delete data source' });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Type Business to confirm'), {
    target: { value: 'Business' },
  });
  fireEvent.click(confirm);
  await waitFor(() =>
    expect(fetchApi).toHaveBeenCalledWith('/workspaces/business-source', { method: 'DELETE' }),
  );
  expect(deleteOrganization).not.toHaveBeenCalled();
});

it('hides the organization-wide confirmation again when the selected source changes', () => {
  const { rerender } = render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Organization controls' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete business' }));
  fireEvent.change(screen.getByLabelText('Type Northstar to confirm'), {
    target: { value: 'Northstar' },
  });
  source = { id: 'another-source', name: 'Business', source_type: 'spreadsheet' };
  rerender(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  );
  expect(screen.queryByRole('button', { name: 'Delete business' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Type Northstar to confirm')).not.toBeInTheDocument();
});
