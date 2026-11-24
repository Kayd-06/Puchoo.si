import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AppProvider } from '../context/AppContext';
import Sidebar from '../components/layout/Sidebar';
import AskData from '../pages/AskData';
import { fetchApi } from '../api/client';

const auth = vi.hoisted(() => ({ user: null, logout: vi.fn() }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));
vi.mock('../api/client', () => ({ fetchApi: vi.fn(), fetchApiBlob: vi.fn() }));

const OWNER = {
  id: 'reupload-owner',
  tenant_id: 'reupload',
  workspace_role: 'owner',
  capabilities: { query_workspace: true, manage_data_sources: true, view_history: true },
};
const EDITOR = {
  id: 'reupload-editor',
  tenant_id: 'reupload',
  workspace_role: 'editor',
  capabilities: { query_workspace: true, view_history: true },
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: false })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function mount() {
  return render(
    <MemoryRouter>
      <AppProvider>
        <Sidebar />
        <AskData />
      </AppProvider>
    </MemoryRouter>,
  );
}

function serve(sources, handlers = {}) {
  fetchApi.mockImplementation(async (path, options) => {
    if (path === '/workspaces/') return sources;
    for (const [suffix, handler] of Object.entries(handlers)) {
      if (path.endsWith(suffix)) return handler(path, options);
    }
    return {};
  });
}

const calls = (suffix) => fetchApi.mock.calls.filter(([path]) => path.endsWith(suffix));

it('tags a wiped source and re-uploads it in place for the owner', async () => {
  auth.user = OWNER;
  const wiped = {
    id: 'ws_school',
    name: 'School 1',
    source_type: 'spreadsheet',
    availability: 'unavailable',
  };
  serve([wiped], {
    '/reupload': async () => ({ ...wiped, availability: 'available' }),
  });
  const view = mount();

  const trigger = await screen.findByRole('button', { name: /select active data source/i });
  await waitFor(() => expect(trigger).toHaveTextContent('School 1'));
  expect(within(trigger).getByText('Needs re-upload')).toBeInTheDocument();
  fireEvent.click(trigger);
  const option = screen.getByRole('option', { name: /School 1/ });
  expect(within(option).getByText('Needs re-upload')).toBeInTheDocument();

  expect(
    screen.getByRole('heading', { name: 'School 1 needs to be uploaded again' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: /ask a question/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Re-upload file' })).toBeEnabled();

  const file = new File(['region,amount\nNorth,1\n'], 'school.csv', { type: 'text/csv' });
  fireEvent.change(view.container.querySelector('input[type="file"]'), {
    target: { files: [file] },
  });

  await waitFor(() => expect(calls('/reupload')).toHaveLength(1));
  const [path, options] = calls('/reupload')[0];
  expect(path).toBe('/workspaces/ws_school/reupload');
  expect(options.method).toBe('POST');
  expect(options.body.get('file').name).toBe('school.csv');
  expect(await screen.findByRole('textbox', { name: /ask a question/i })).toBeInTheDocument();
  expect(within(trigger).queryByText('Needs re-upload')).not.toBeInTheDocument();
  expect(calls('/upload')).toHaveLength(0);
});

it('shows the re-upload error inside the card', async () => {
  auth.user = OWNER;
  serve([{ id: 'ws_a', name: 'Sales', source_type: 'spreadsheet', availability: 'unavailable' }], {
    '/reupload': async () => {
      const failure = new Error('The uploaded file is too large.');
      failure.status = 413;
      throw failure;
    },
  });
  const view = mount();
  await screen.findByRole('button', { name: 'Re-upload file' });
  fireEvent.change(view.container.querySelector('input[type="file"]'), {
    target: { files: [new File(['x'], 'big.csv', { type: 'text/csv' })] },
  });
  expect(await screen.findByRole('alert')).toHaveTextContent('The uploaded file is too large.');
});

it('tells members without upload rights who can fix it', async () => {
  auth.user = EDITOR;
  serve([{ id: 'ws_b', name: 'Ledger', source_type: 'tally_report', availability: 'unavailable' }]);
  mount();
  expect(await screen.findByText(/Ask the workspace owner or an admin/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Re-upload file' })).not.toBeInTheDocument();
});

it('stops after one 409 instead of retrying every part of the question', async () => {
  auth.user = OWNER;
  serve([{ id: 'ws_c', name: 'Results', source_type: 'spreadsheet' }], {
    '/generate': async () => {
      const failure = new Error('This data source needs to be uploaded again.');
      failure.status = 409;
      throw failure;
    },
  });
  mount();
  const box = await screen.findByRole('textbox', { name: /ask a question/i });
  fireEvent.change(box, {
    target: {
      value: 'What is the total? Which class scored highest? How many students passed? Who topped?',
    },
  });
  fireEvent.keyDown(box, { key: 'Enter' });

  expect(
    await screen.findByRole('heading', { name: 'Results needs to be uploaded again' }),
  ).toBeInTheDocument();
  expect(calls('/generate')).toHaveLength(1);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  // The question box is gone, so Enter can't send more requests.
  expect(screen.queryByRole('textbox', { name: /ask a question/i })).not.toBeInTheDocument();
});
