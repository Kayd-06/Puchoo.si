import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AppProvider, useAppContext } from '../AppContext';
import { fetchApi } from '../../api/client';

vi.mock('../../api/client', () => ({ fetchApi: vi.fn() }));

function Sources() {
  const { activeWorkspace, workspaces } = useAppContext();
  return (
    <p>
      {activeWorkspace?.name || 'No shared source'} ({workspaces.length})
    </p>
  );
}

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it('discovers an owner source added after the member signed in', async () => {
  let sources = [];
  fetchApi.mockImplementation(async (path) => (path === '/workspaces/' ? sources : {}));
  render(
    <AppProvider>
      <Sources />
    </AppProvider>,
  );
  await waitFor(() => expect(fetchApi).toHaveBeenCalledWith('/settings/profile'));
  expect(screen.getByText('No shared source (0)')).toBeInTheDocument();
  sources = [{ id: 'owner-source', name: 'Shared reporting' }];
  fireEvent(window, new Event('focus'));
  await screen.findByText('Shared reporting (1)');
  expect(localStorage.getItem('pucho_active_workspace')).toBe('owner-source');
});

it('does not restore shared sources from a response that arrives after sign-out', async () => {
  let resolveSources;
  fetchApi.mockImplementation((path) =>
    path === '/workspaces/'
      ? new Promise((resolve) => {
          resolveSources = resolve;
        })
      : Promise.resolve({}),
  );
  render(
    <AppProvider>
      <Sources />
    </AppProvider>,
  );
  fireEvent(window, new Event('puchoo:unauthorized'));
  await act(async () => resolveSources([{ id: 'old-source', name: 'Old tenant' }]));
  expect(screen.getByText('No shared source (0)')).toBeInTheDocument();
  expect(localStorage.getItem('pucho_active_workspace')).toBeNull();
});

function UploadedSource() {
  const { addWorkspace, setActiveWorkspaceId } = useAppContext();
  return (
    <button
      type="button"
      onClick={() => {
        addWorkspace({ id: 'new-upload', name: 'Fresh upload' });
        setActiveWorkspaceId('new-upload');
      }}
    >
      Finish upload
    </button>
  );
}

it('keeps a just-uploaded source when an older list response arrives later', async () => {
  const pending = [];
  fetchApi.mockImplementation((path) =>
    path === '/workspaces/' ? new Promise((resolve) => pending.push(resolve)) : Promise.resolve({}),
  );
  render(
    <AppProvider>
      <Sources />
      <UploadedSource />
    </AppProvider>,
  );
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0]([{ id: 'old-source', name: 'Old source' }]));
  await screen.findByText('Old source (1)');
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(pending).toHaveLength(2));
  fireEvent.click(screen.getByRole('button', { name: 'Finish upload' }));
  await act(async () => pending[1]([{ id: 'old-source', name: 'Old source' }]));
  expect(screen.getByText('Fresh upload (2)')).toBeInTheDocument();
  expect(localStorage.getItem('pucho_active_workspace')).toBe('new-upload');
});

function RemoveSource() {
  const { activeWorkspace, removeWorkspace } = useAppContext();
  return (
    <button onClick={() => removeWorkspace(activeWorkspace.id)}>Delete selected source</button>
  );
}

it('selects another source after deletion and ignores a stale list response', async () => {
  const pending = [];
  fetchApi.mockImplementation((path) =>
    path === '/workspaces/' ? new Promise((resolve) => pending.push(resolve)) : Promise.resolve({}),
  );
  render(
    <AppProvider>
      <Sources />
      <RemoveSource />
    </AppProvider>,
  );
  await waitFor(() => expect(pending).toHaveLength(1));
  const sources = [
    { id: 'school', name: 'School1' },
    { id: 'reports', name: 'Reports' },
  ];
  await act(async () => pending[0](sources));
  await screen.findByText('School1 (2)');
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(pending).toHaveLength(2));
  fireEvent.click(screen.getByRole('button', { name: 'Delete selected source' }));
  expect(screen.getByText('Reports (1)')).toBeInTheDocument();
  await act(async () => pending[1](sources));
  expect(screen.getByText('Reports (1)')).toBeInTheDocument();
  expect(localStorage.getItem('pucho_active_workspace')).toBe('reports');
});
