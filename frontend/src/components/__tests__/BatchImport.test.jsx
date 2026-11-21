import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BatchImport, {
  MAX_BATCH_FILES,
  mismatchedIds,
  normalizeCompanyName,
} from '../connect/BatchImport';
import { fetchApi } from '../../api/client';

vi.mock('../../api/client', () => ({ fetchApi: vi.fn() }));

afterEach(() => {
  vi.clearAllMocks();
});

function tallyPreview(name, overrides = {}) {
  const table = name.toLowerCase().replace(/\W+/g, '_');
  return {
    kind: 'tally',
    report_type: 'voucher_register',
    report_name: name,
    company_name: 'Demo Traders (26-27)',
    period_start: '2026-11-01',
    period_end: '2026-11-30',
    table,
    columns: ['date', 'party', 'debit_amount'],
    row_count: 3,
    sample_rows: [{ date: '2026-11-02', party: 'Sharma Hardware Stores', debit_amount: 48250 }],
    totals_check: {
      status: 'pass',
      checks: [{ label: 'Debit', expected: 1, actual: 1, ok: true }],
    },
    warnings: [],
    company: { suggested_mode: 'new', suggested_table: table, different_company: false },
    ...overrides,
  };
}

const PLAIN = {
  kind: 'table',
  columns: ['month', 'sales_target'],
  row_count: 2,
  sample_rows: [{ month: 'Nov', sales_target: 400000 }],
  sheets: [{ name: 'Targets', rows: 2, columns: ['month', 'sales_target'] }],
  suggested_table: 'sales_targets',
};
const UNKNOWN = {
  kind: 'unknown',
  rows: [
    ['Demo Traders stock summary', '', ''],
    ['', '', ''],
    ['Item', 'Qty', 'Value'],
    ['Cement', '10', '3,500.00'],
  ],
  suggested_header_row: 2,
  suggested_table: 'stock_summary',
};

const REPORTS = [
  'Sales Register',
  'Receipt Register',
  'Payment Register',
  'Purchase Register',
  'Journal Register',
  'Day Book',
  'Credit Note Register',
  'Debit Note Register',
];

function file(name) {
  return new File(['x'], name, { type: 'text/csv' });
}

function mockServer(previews, { failing = [], mismatch = [] } = {}) {
  const confirms = [];
  fetchApi.mockImplementation(async (url, options) => {
    const name = options.body.get('file').name;
    if (url.endsWith('/import/preview')) {
      const preview = previews[name];
      if (preview instanceof Error) throw preview;
      return preview;
    }
    const fields = Object.fromEntries(
      [...options.body.entries()].filter(([key]) => key !== 'file'),
    );
    confirms.push({ fileName: name, ...fields });
    if (failing.includes(name)) throw new Error('The uploaded file could not be processed.');
    if (mismatch.includes(name) && !fields.confirm_company_mismatch) {
      const failure = new Error('This report is from Other Agencies.');
      failure.code = 'company_mismatch';
      throw failure;
    }
    const preview = previews[name];
    return {
      workspace: {
        id: fields.target_workspace_id || 'ws_new',
        name: 'Demo Traders',
        file_count: 1,
      },
      import:
        preview.kind === 'tally'
          ? { kind: 'tally', table: preview.table, rows_added: 3, rows_skipped: 0 }
          : { kind: 'table', table: fields.table_name, tables: [fields.table_name], rows: 2 },
      added_to_existing: Boolean(fields.target_workspace_id),
    };
  });
  return confirms;
}

function renderBatch(files, props = {}) {
  const onCompanySaved = vi.fn();
  const onAddMore = vi.fn();
  render(
    <MemoryRouter>
      <BatchImport
        files={files}
        companies={props.companies || []}
        initialTarget={props.initialTarget}
        onCompanySaved={onCompanySaved}
        onAddMore={onAddMore}
        onClose={vi.fn()}
      />
    </MemoryRouter>,
  );
  return { onCompanySaved, onAddMore };
}

async function waitForPreviews() {
  await waitFor(() => expect(screen.queryByText(/Checking this file/)).toBeNull());
}

describe('batch upload', () => {
  it('compares company names like the server', () => {
    expect(MAX_BATCH_FILES).toBe(10);
    expect(normalizeCompanyName('Demo  Traders (26-27)')).toBe('demo traders');
    expect(normalizeCompanyName('demo traders (2027-28)')).toBe('demo traders');
    const items = [
      { id: 1, status: 'ready', preview: tallyPreview('Sales Register') },
      { id: 2, status: 'ready', preview: tallyPreview('Day Book', { company_name: 'Other Co' }) },
      {
        id: 3,
        status: 'ready',
        cancelled: true,
        preview: tallyPreview('X', { company_name: 'Y' }),
      },
    ];
    expect([...mismatchedIds(items, '')]).toEqual([2]);
  });

  it('shows a full preview card for every file, mixed Tally and plain', async () => {
    const files = [
      file('Sales Register.xlsx'),
      file('Stock Summary.csv'),
      file('Sales Targets.xlsx'),
    ];
    mockServer({
      'Sales Register.xlsx': tallyPreview('Sales Register'),
      'Stock Summary.csv': UNKNOWN,
      'Sales Targets.xlsx': PLAIN,
    });
    renderBatch(files);
    await waitForPreviews();
    expect(screen.getAllByRole('listitem', { hidden: false })).toHaveLength(3);
    expect(screen.getByRole('heading', { name: 'Sales Register' })).toBeInTheDocument();
    expect(screen.getByText('Totals match Tally (1 check)')).toBeInTheDocument();
    expect(screen.getByText('Sharma Hardware Stores')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Use row 3 as the header' })).toBeChecked();
    expect(screen.getByRole('columnheader', { name: 'sales_target' })).toBeInTheDocument();
    expect(
      screen.getAllByRole('textbox', { name: 'Table name' }).map((input) => input.value),
    ).toEqual(['sales_register', 'stock_summary', 'sales_targets']);
    expect(screen.queryByText(/one at a time/)).toBeNull();
    expect(fetchApi).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('button', { name: /Save 3 files to a new company/ })).toBeEnabled();
  });

  it('cancels 2 of 10 files and saves the other 8 into one new company', async () => {
    const names = [
      ...REPORTS.map((name) => `${name}.xlsx`),
      'Stock Summary.csv',
      'Sales Targets.xlsx',
    ];
    const previews = Object.fromEntries(
      names.map((name, index) => [
        name,
        index < REPORTS.length ? tallyPreview(REPORTS[index]) : index === 8 ? UNKNOWN : PLAIN,
      ]),
    );
    const confirms = mockServer(previews);
    const { onCompanySaved, onAddMore } = renderBatch(names.map(file));
    await waitForPreviews();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel Day Book.xlsx' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel Sales Targets.xlsx' }));
    const cancelledCard = screen.getByRole('listitem', { name: 'Day Book.xlsx' });
    expect(cancelledCard).toHaveTextContent('Cancelled. This file will not be uploaded.');
    expect(screen.getByRole('textbox', { name: 'New company name' })).toHaveValue(
      'Demo Traders (26-27)',
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'New company name' }), {
      target: { value: 'Demo Traders' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save 8 files to a new company/ }));
    });
    await screen.findByRole('heading', { name: '8 files added to Demo Traders' });

    expect(confirms).toHaveLength(8);
    expect(confirms.map((item) => item.fileName)).not.toContain('Day Book.xlsx');
    expect(confirms.map((item) => item.fileName)).not.toContain('Sales Targets.xlsx');
    expect(confirms[0]).toMatchObject({ fileName: 'Sales Register.xlsx', name: 'Demo Traders' });
    expect(confirms[0].target_workspace_id).toBeUndefined();
    expect(confirms.slice(1).every((item) => item.target_workspace_id === 'ws_new')).toBe(true);
    expect(confirms.find((item) => item.fileName === 'Receipt Register.xlsx').mode).toBe('auto');
    expect(confirms.find((item) => item.fileName === 'Stock Summary.csv')).toMatchObject({
      header_row: '2',
      table_name: 'stock_summary',
    });
    expect(onCompanySaved).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ id: 'ws_new' }),
      false,
    );
    expect(onCompanySaved).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'ws_new' }),
      true,
    );
    expect(screen.getByRole('status')).toHaveTextContent('8 files added, 2 cancelled.');
    expect(screen.getAllByText('Not uploaded (cancelled). You can add it later.')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Add more files to Demo Traders' }));
    expect(onAddMore).toHaveBeenCalledWith('ws_new');
  });

  it('adds the 2 later files to the existing company without touching it', async () => {
    const confirms = mockServer({
      'Day Book.xlsx': tallyPreview('Day Book'),
      'Sales Targets.xlsx': PLAIN,
    });
    renderBatch([file('Day Book.xlsx'), file('Sales Targets.xlsx')], {
      companies: [{ id: 'ws_1', name: 'Demo Traders', file_count: 8 }],
      initialTarget: 'ws_1',
    });
    await waitForPreviews();
    expect(fetchApi.mock.calls[0][1].body.get('target_workspace_id')).toBe('ws_1');
    expect(
      screen.getByText(/Files are added next to the ones already in Demo Traders/),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save 2 files to Demo Traders/ }));
    });
    await screen.findByRole('heading', { name: '2 files added to Demo Traders' });
    expect(confirms.every((item) => item.target_workspace_id === 'ws_1')).toBe(true);
    expect(confirms.every((item) => item.name === undefined)).toBe(true);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Files already in the company were not changed.',
    );
  });

  it('keeps saving when one file fails, and reports why', async () => {
    const previews = {
      'Sales Register.xlsx': tallyPreview('Sales Register'),
      'Broken.xlsx': new Error('The uploaded file content does not match its declared format.'),
      'Receipt Register.xlsx': tallyPreview('Receipt Register'),
      'Payment Register.xlsx': tallyPreview('Payment Register'),
    };
    const confirms = mockServer(previews, { failing: ['Receipt Register.xlsx'] });
    renderBatch(Object.keys(previews).map(file));
    await waitForPreviews();
    expect(screen.getByRole('alert')).toHaveTextContent(
      "This file can't be saved: The uploaded file content does not match its declared format.",
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save 3 files/ }));
    });
    await screen.findByRole('heading', { name: '2 files added to Demo Traders' });
    expect(confirms.map((item) => item.fileName)).toEqual([
      'Sales Register.xlsx',
      'Receipt Register.xlsx',
      'Payment Register.xlsx',
    ]);
    const failed = screen.getByText('Receipt Register.xlsx').closest('li');
    expect(failed).toHaveTextContent('Not saved: The uploaded file could not be processed.');
    expect(screen.getByText('Broken.xlsx').closest('li')).toHaveTextContent('Not saved:');
    expect(screen.getByText('Payment Register.xlsx').closest('li')).toHaveTextContent(
      '3 rows added in the table payment_register.',
    );
  });

  it('asks about a different company for that file only', async () => {
    const previews = {
      'Sales Register.xlsx': tallyPreview('Sales Register'),
      'Other Sales.xlsx': tallyPreview('Purchase Register', {
        company_name: 'Other Agencies (26-27)',
      }),
      'Receipt Register.xlsx': tallyPreview('Receipt Register'),
    };
    const confirms = mockServer(previews, { mismatch: ['Other Sales.xlsx'] });
    renderBatch(Object.keys(previews).map(file));
    await waitForPreviews();
    const card = screen.getByRole('alert');
    expect(card).toHaveTextContent('The header says Other Agencies (26-27)');
    expect(screen.getByRole('button', { name: /Save 2 files/ })).toBeEnabled();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save 2 files/ }));
    });
    await screen.findByRole('heading', { name: '2 files added to Demo Traders' });
    expect(confirms.map((item) => item.fileName)).toEqual([
      'Sales Register.xlsx',
      'Receipt Register.xlsx',
    ]);
    const pending = screen.getByText('Other Sales.xlsx').closest('li');
    expect(pending).toHaveTextContent('Not saved yet');
    await act(async () => {
      fireEvent.click(within(pending).getByRole('button', { name: 'Add this file anyway' }));
    });
    expect(confirms.at(-1)).toMatchObject({
      fileName: 'Other Sales.xlsx',
      target_workspace_id: 'ws_new',
      confirm_company_mismatch: 'true',
    });
    await waitFor(() =>
      expect(screen.getByText('Other Sales.xlsx').closest('li')).toHaveTextContent('3 rows added'),
    );
  });

  it('names each ledger after its file so two ledgers are easy to tell apart', async () => {
    const ledger = tallyPreview('Ledger Vouchers', { table: 'ledger_vouchers' });
    const confirms = mockServer({
      'Bank Book.xlsx': ledger,
      'Sales Register.xlsx': tallyPreview('Sales Register'),
    });
    renderBatch([file('Bank Book.xlsx'), file('Sales Register.xlsx')]);
    await waitForPreviews();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Save 2 files/ }));
    });
    await screen.findByRole('heading', { name: '2 files added to Demo Traders' });
    expect(confirms[0]).toMatchObject({ fileName: 'Bank Book.xlsx', label: 'Bank Book' });
    expect(confirms[1].label).toBeUndefined();
  });
});
