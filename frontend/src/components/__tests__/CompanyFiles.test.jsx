import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CompanyPicker, {
  NEW_COMPANY,
  companyOptions,
  defaultCompanyTarget,
  fileCountLabel,
} from '../connect/CompanyPicker';
import CompanyFilesPanel, { addedDate, filePeriod } from '../workspace/CompanyFilesPanel';
import { fetchApi } from '../../api/client';

const state = vi.hoisted(() => ({ user: null, app: null }));

vi.mock('../../api/client', () => ({ fetchApi: vi.fn() }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock('../../context/AppContext', () => ({ useAppContext: () => state.app }));
vi.mock('../../hooks/useChatState', () => ({
  chatScope: (user, id) => `scope:${id}`,
  invalidateHistory: vi.fn(),
}));

const company = {
  id: 'ws_1',
  name: 'Demo Traders',
  source_type: 'tally',
  availability: 'available',
  file_count: 2,
};
const other = {
  id: 'ws_2',
  name: 'Demo Traders ledger',
  source_type: 'tally',
  availability: 'available',
  file_count: 1,
};
const files = [
  {
    table: 'sales_register',
    label: 'Sales Register',
    kind: 'tally',
    rows: 1200,
    period_start: '2026-04-01',
    period_end: '2026-06-30',
    added_at: '2026-11-01T10:00:00Z',
  },
  {
    table: 'stock_list',
    label: 'Stock list',
    kind: 'table',
    rows: 40,
    added_at: '2026-11-02T10:00:00Z',
  },
];

function managerApp(overrides = {}) {
  return {
    activeWorkspace: company,
    workspaces: [
      company,
      other,
      { id: 'srv', name: 'Server', source_type: 'server', availability: 'available' },
    ],
    updateWorkspace: vi.fn(),
    removeWorkspace: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  state.user = { id: 1, capabilities: { manage_data_sources: true, query_workspace: true } };
  state.app = managerApp();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('company picker', () => {
  it('lists companies with a file count and a new company option', () => {
    const onChange = vi.fn();
    const companies = companyOptions(state.app.workspaces);
    expect(companies.map((item) => item.id)).toEqual(['ws_1', 'ws_2']);
    expect(defaultCompanyTarget(companies, 'ws_2')).toBe('ws_2');
    expect(defaultCompanyTarget(companies, 'srv')).toBe(NEW_COMPANY);
    expect(fileCountLabel(1)).toBe('1 file');
    expect(fileCountLabel(3)).toBe('3 files');

    const { rerender } = render(
      <CompanyPicker companies={companies} value="ws_1" onChange={onChange} />,
    );
    expect(screen.getByRole('option', { name: 'Demo Traders (2 files)' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'New company name' })).toBeNull();
    fireEvent.change(screen.getByRole('combobox', { name: 'Add to company' }), {
      target: { value: NEW_COMPANY },
    });
    expect(onChange).toHaveBeenCalledWith(NEW_COMPANY);
    rerender(<CompanyPicker companies={companies} value={NEW_COMPANY} onChange={onChange} />);
    expect(screen.getByRole('textbox', { name: 'New company name' })).toBeInTheDocument();
  });
});

describe('company files panel', () => {
  it('formats periods and dates in plain words', () => {
    expect(filePeriod(files[0])).toBe('1 Apr 2026 to 30 Jun 2026');
    expect(filePeriod({ report_date: '2026-03-31' })).toBe('As at 31 Mar 2026');
    expect(filePeriod({})).toBe('No period in file');
    expect(addedDate(null)).toBe('Not recorded');
  });

  it('lists files and removes one after an inline confirm', async () => {
    fetchApi.mockImplementation(async (url, options = {}) => {
      if (options.method === 'DELETE')
        return { removed: 'stock_list', workspace: { ...company, file_count: 1 } };
      return { files };
    });
    render(<CompanyFilesPanel />);
    expect(await screen.findByText('Sales Register')).toBeInTheDocument();
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(screen.getByText('1 Apr 2026 to 30 Jun 2026')).toBeInTheDocument();

    const row = screen.getByText('Stock list').closest('li');
    fireEvent.click(within(row).getByRole('button', { name: /Remove/ }));
    await act(async () => {
      fireEvent.click(within(row).getByRole('button', { name: 'Remove file' }));
    });
    expect(fetchApi).toHaveBeenCalledWith('/workspaces/ws_1/files/stock_list', {
      method: 'DELETE',
    });
    expect(state.app.updateWorkspace).toHaveBeenCalledWith({ ...company, file_count: 1 });
    expect(await screen.findByRole('status')).toHaveTextContent('Stock list was removed.');
  });

  it('asks before replacing a file with one from a different company', async () => {
    let attempts = 0;
    fetchApi.mockImplementation(async (url, options = {}) => {
      if (url.endsWith('/replace')) {
        attempts += 1;
        if (!options.body.get('confirm_company_mismatch')) {
          const failure = new Error('This file looks like it is from Other Agencies.');
          failure.code = 'company_mismatch';
          throw failure;
        }
        return { import: { rows: 5 }, workspace: company };
      }
      return { files };
    });
    render(<CompanyFilesPanel />);
    await screen.findByText('Sales Register');
    const file = new File(['x'], 'sales.xlsx');
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Replace file for Sales Register'), {
        target: { files: [file] },
      });
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Other Agencies');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Use it anyway' }));
    });
    expect(attempts).toBe(2);
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Sales Register was replaced with sales.xlsx.',
    );
  });

  it('moves another data source into the company after a mismatch confirm', async () => {
    fetchApi.mockImplementation(async (url) => {
      if (url.includes('/merge-preview')) {
        return {
          tables: { sales_register: 'sales_register_2' },
          renamed: { sales_register: 'sales_register_2' },
          different_company: true,
        };
      }
      if (url.endsWith('/merge')) {
        return {
          workspace: { ...company, file_count: 3 },
          removed_workspace_id: 'ws_2',
          history_moved: 4,
        };
      }
      return { files };
    });
    render(<CompanyFilesPanel />);
    await screen.findByText('Sales Register');
    const select = screen.getByRole('combobox', { name: 'Data source to move' });
    expect(within(select).queryByRole('option', { name: /Server/ })).toBeNull();
    await act(async () => {
      fireEvent.change(select, { target: { value: 'ws_2' } });
    });
    expect(await screen.findByRole('note')).toHaveTextContent(
      '1 table will move. Renamed to avoid a clash: sales_register → sales_register_2.',
    );
    const move = screen.getByRole('button', { name: /Move into Demo Traders/ });
    expect(move).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    await act(async () => {
      fireEvent.click(move);
    });
    expect(fetchApi).toHaveBeenCalledWith('/workspaces/ws_1/merge', {
      method: 'POST',
      body: JSON.stringify({ source_workspace_id: 'ws_2', confirm_company_mismatch: true }),
    });
    expect(state.app.removeWorkspace).toHaveBeenCalledWith('ws_2');
    expect(state.app.updateWorkspace).toHaveBeenCalledWith({ ...company, file_count: 3 });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'Demo Traders ledger was moved into Demo Traders. 4 earlier questions were kept',
      ),
    );
  });

  it('shows the list without actions to editors and hides for server sources', async () => {
    state.user = { id: 2, capabilities: { query_workspace: true } };
    fetchApi.mockResolvedValue({ files });
    const { unmount } = render(<CompanyFilesPanel />);
    await screen.findByText('Sales Register');
    expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Data source to move' })).toBeNull();
    unmount();

    state.app = managerApp({
      activeWorkspace: { id: 'srv', name: 'Server', source_type: 'server' },
    });
    const { container } = render(<CompanyFilesPanel />);
    expect(container).toBeEmptyDOMElement();
  });
});
