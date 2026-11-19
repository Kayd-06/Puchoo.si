import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AskData from '../AskData';

const state = vi.hoisted(() => ({ user: null, workspace: null, refresh: vi.fn() }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock('../../context/AppContext', () => ({
  useAppContext: () => ({
    activeWorkspace: state.workspace,
    activeWorkspaceId: state.workspace?.id || null,
    refreshWorkspaces: state.refresh,
  }),
}));
vi.mock('../../api/client', () => ({ fetchApi: vi.fn(), fetchApiBlob: vi.fn() }));

function mount() {
  return render(
    <MemoryRouter>
      <AskData />
    </MemoryRouter>,
  );
}
function role(name) {
  state.user = {
    id: `empty-${name}`,
    workspace_role: name,
    capabilities: { manage_data_sources: ['owner', 'admin'].includes(name) },
  };
}
beforeEach(() => {
  state.workspace = null;
  state.refresh.mockReset().mockResolvedValue([]);
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
});
afterEach(() => vi.unstubAllGlobals());

it.each(['editor', 'viewer'])(
  '%s sees a waiting state without upload links or a composer',
  (name) => {
    role(name);
    mount();
    expect(screen.getByRole('heading', { name: 'Waiting for shared data.' })).toBeInTheDocument();
    expect(screen.getByText(/workspace owner or admin/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /connect a data source/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByText('Private workspace')).not.toBeInTheDocument();
  },
);

it.each(['owner', 'admin'])('%s keeps the connect action', (name) => {
  role(name);
  mount();
  expect(screen.getByRole('link', { name: /connect a data source/i })).toHaveAttribute(
    'href',
    '/connect',
  );
  expect(screen.queryByText('Waiting for shared data.')).not.toBeInTheDocument();
});

it('lets a member retry after a refresh fails without crashing', async () => {
  role('editor');
  state.refresh.mockRejectedValueOnce(new Error('offline'));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Check for shared data' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Could not check for shared data. Please try again.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Check for shared data' }));
  await waitFor(() => expect(state.refresh).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

it('shows the editor composer when shared data becomes available', () => {
  role('editor');
  const view = mount();
  state.workspace = { id: 'shared-school', name: 'School' };
  view.rerender(
    <MemoryRouter>
      <AskData />
    </MemoryRouter>,
  );
  expect(screen.queryByText('Waiting for shared data.')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toBeInTheDocument();
});

it('keeps viewers on approved history once data is shared', () => {
  role('viewer');
  state.workspace = { id: 'shared-school', name: 'School' };
  mount();
  expect(screen.getByRole('link', { name: /open history/i })).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});
