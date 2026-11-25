import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchApi } from '../../api/client';
import ConnectData from '../ConnectData';

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'owner-id',
      role: 'owner',
      workspace_role: 'owner',
      capabilities: { manage_data_sources: true },
    },
  }),
}));
vi.mock('../../context/AppContext', () => ({
  useAppContext: () => ({
    workspaces: [],
    activeWorkspace: null,
    activeWorkspaceId: null,
    setActiveWorkspaceId: vi.fn(),
    addWorkspace: vi.fn(),
    updateWorkspace: vi.fn(),
  }),
}));
vi.mock('../../api/client', () => ({ fetchApi: vi.fn() }));

// Synthetic company data only.
const csv = () => new File(['voucher,amount\n1,20'], 'demo-sales.csv', { type: 'text/csv' });

const deferred = () => {
  let reject;
  const promise = new Promise((_, fail) => {
    reject = fail;
  });
  return { promise, reject };
};

function chooseFile() {
  render(
    <MemoryRouter>
      <ConnectData />
    </MemoryRouter>,
  );
  fireEvent.change(document.getElementById('fileUpload'), { target: { files: [csv()] } });
}

describe('connect data states', () => {
  beforeEach(() => fetchApi.mockReset());

  it('locks both tabs and says Checking while a single file is only being previewed', async () => {
    const preview = deferred();
    fetchApi.mockReturnValueOnce(preview.promise);
    chooseFile();
    fireEvent.click(screen.getByRole('button', { name: /create company/i }));
    expect(screen.getByRole('tab', { name: /upload files/i })).toBeDisabled();
    expect(screen.getByRole('tab', { name: /connect server/i })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Checking your file');
    expect(screen.getByText('Checking')).toBeInTheDocument();
    expect(screen.queryByText('Importing')).not.toBeInTheDocument();
    // The status lives outside the file input label, so it is not part of its name.
    expect(screen.getByRole('status').closest('label')).toBeNull();
    await act(async () => preview.reject(new Error('Preview stopped in test')));
    expect(screen.getByRole('tab', { name: /upload files/i })).toBeEnabled();
  });

  it('shows one clear success line after a file import', async () => {
    fetchApi.mockResolvedValueOnce({ kind: 'table' }).mockResolvedValueOnce({
      id: 'ws-1',
      name: 'Demo Traders Pvt Ltd',
      source_type: 'spreadsheet',
    });
    chooseFile();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /create company/i }));
    });
    expect(await screen.findByRole('heading', { name: 'Import Successful' })).toBeInTheDocument();
    expect(screen.getAllByText(/1 file uploaded\./)).toHaveLength(1);
    expect(screen.queryByText(/uploaded successfully/)).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /upload files/i })).toBeEnabled();
  });

  it('shows Connection Successful with no file count for a server source', async () => {
    fetchApi.mockResolvedValueOnce({
      id: 'ws-2',
      name: 'Demo Warehouse',
      source_type: 'server',
    });
    render(
      <MemoryRouter>
        <ConnectData />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('tab', { name: /connect server/i }));
    const fields = {
      'Workspace name': 'Demo Warehouse',
      'Server host': 'db.example.test',
      'Database name': 'demo',
      'Read-only username': 'reader',
      Password: 'not-a-real-password',
    };
    for (const [label, value] of Object.entries(fields))
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /connect source/i }));
    });
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Connection Successful' })).toBeInTheDocument(),
    );
    expect(screen.queryByText(/file uploaded|files uploaded/)).not.toBeInTheDocument();
    expect(screen.getByText(/available for verified, read-only analysis/)).toBeInTheDocument();
  });
});
