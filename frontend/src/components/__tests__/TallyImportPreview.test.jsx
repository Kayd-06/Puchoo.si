import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import TallyImportPreview, {
  formatReportDate,
  reportPeriod,
  totalsSummary,
} from '../connect/TallyImportPreview';
import HeaderRowPicker from '../connect/HeaderRowPicker';
import { NUMBERS_FILE_MESSAGE, numbersFileError } from '../../pages/ConnectData';

const salesPreview = {
  kind: 'tally',
  report_type: 'voucher_register',
  report_name: 'Sales Register',
  company_name: 'Demo Traders (26-27)',
  period_start: '2026-11-01',
  period_end: '2026-11-30',
  report_date: '2026-11-30',
  table: 'sales_register',
  columns: [
    'date',
    'party',
    'voucher_type',
    'voucher_no',
    'debit_amount',
    'credit_amount',
    'period_start',
    'period_end',
    'source_report',
  ],
  row_count: 12,
  sample_rows: [
    {
      date: '2026-11-01',
      party: 'Sharma Hardware Stores',
      voucher_type: 'Sales',
      voucher_no: '820',
      debit_amount: 48250,
      credit_amount: null,
      period_start: '2026-11-01',
      period_end: '2026-11-30',
      source_report: 'Sales Register',
    },
  ],
  totals_check: {
    status: 'pass',
    checks: [{ label: 'Debit amount', expected: 476000, actual: 476000, ok: true }],
  },
  warnings: [],
};

describe('Tally import preview', () => {
  it('shows the report type, period, cleaned rows and a passing totals check', () => {
    render(
      <TallyImportPreview
        preview={salesPreview}
        fileName="Sales Register.xlsx"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Tally report detected')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Sales Register' })).toBeInTheDocument();
    expect(screen.getByText('1 Nov 2026 to 30 Nov 2026')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Totals match Tally (1 check)');
    expect(screen.getByText('Sharma Hardware Stores')).toBeInTheDocument();
    expect(screen.getByText('48,250.00')).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'source_report' })).toBeNull();
  });

  it('lists failed totals and warnings, and wires confirm and cancel', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const failing = {
      ...salesPreview,
      totals_check: {
        status: 'fail',
        checks: [{ label: 'Debit amount', expected: 476100, actual: 476000, ok: false }],
      },
      warnings: ['Suspense A/c is present.'],
    };
    render(
      <TallyImportPreview
        preview={failing}
        fileName="s.xlsx"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('1 total do not match Tally');
    expect(screen.getByText(/rows 4,76,000.00, Tally 4,76,100.00/)).toBeInTheDocument();
    expect(screen.getByText('Suspense A/c is present.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Create company and save/ }));
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('adds to an existing company and explains skipped duplicates', () => {
    const onTargetChange = vi.fn();
    const onChoiceChange = vi.fn();
    const onConfirm = vi.fn();
    render(
      <TallyImportPreview
        preview={{
          ...salesPreview,
          company: {
            tables: ['sales_register'],
            append_candidates: ['sales_register'],
            suggested_mode: 'append',
            suggested_table: 'sales_register',
            different_company: false,
          },
          append: { existing_vouchers: 12, new_rows: 0, different_company: false },
        }}
        fileName="s.xlsx"
        companies={[{ id: 'ws_1', name: 'Demo Traders', file_count: 2 }]}
        targetId="ws_1"
        onTargetChange={onTargetChange}
        onChoiceChange={onChoiceChange}
        onConfirm={onConfirm}
      />,
    );
    expect(screen.getByRole('note')).toHaveTextContent(
      '12 vouchers are already saved and will be skipped. 0 new rows will be added.',
    );
    const picker = screen.getByRole('combobox', { name: 'Add to company' });
    expect(screen.getByRole('option', { name: 'Demo Traders (2 files)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '+ New company' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Existing table' })).toHaveValue('sales_register');
    fireEvent.click(screen.getByRole('radio', { name: 'Save as a new table' }));
    expect(onChoiceChange).toHaveBeenCalledWith(expect.objectContaining({ mode: 'new' }));
    fireEvent.click(screen.getByRole('button', { name: /Add to Demo Traders/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.change(picker, { target: { value: '__new__' } });
    expect(onTargetChange).toHaveBeenCalledWith('__new__');
  });

  it('blocks saving into a different company until the user confirms', () => {
    const onMismatch = vi.fn();
    const preview = {
      ...salesPreview,
      company_name: 'Other Agencies (26-27)',
      company: {
        tables: ['sales_register'],
        append_candidates: [],
        suggested_mode: 'new',
        suggested_table: 'sales_register_2',
        different_company: true,
        company_on_file: 'Demo Traders',
      },
    };
    const props = {
      preview,
      fileName: 's.xlsx',
      companies: [{ id: 'ws_1', name: 'Demo Traders', file_count: 1 }],
      targetId: 'ws_1',
      onMismatchConfirmedChange: onMismatch,
    };
    const { rerender } = render(<TallyImportPreview {...props} />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The report header says Other Agencies (26-27), but Demo Traders holds reports from Demo Traders.',
    );
    expect(screen.getByRole('textbox', { name: 'Table name' })).toHaveValue('sales_register_2');
    const save = screen.getByRole('button', { name: /Add to Demo Traders/ });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Add it to Demo Traders anyway/ }));
    expect(onMismatch).toHaveBeenCalledWith(true);
    rerender(<TallyImportPreview {...props} mismatchConfirmed />);
    expect(screen.getByRole('button', { name: /Add to Demo Traders/ })).toBeEnabled();
  });

  it('needs a table name before saving', () => {
    render(
      <TallyImportPreview
        preview={salesPreview}
        fileName="s.xlsx"
        choice={{ mode: 'new', table: '   ', label: '' }}
      />,
    );
    expect(screen.getByRole('button', { name: /Create company and save/ })).toBeDisabled();
  });

  it('formats dates and periods', () => {
    expect(formatReportDate('2026-11-19')).toBe('19 Nov 2026');
    expect(reportPeriod({ report_date: '2026-11-19' })).toBe('As at 19 Nov 2026');
    expect(totalsSummary({ status: 'unavailable', checks: [] })).toBe(
      'No Tally total to check against',
    );
  });
});

describe('Header row picker', () => {
  it('marks the chosen row and the skipped title rows', () => {
    const onSelect = vi.fn();
    const rows = [
      ['Demo Traders', '', ''],
      ['', '', ''],
      ['Item', 'Qty', 'Value'],
    ];
    const { container } = render(
      <HeaderRowPicker fileName="stock.csv" rows={rows} selectedRow={2} onSelect={onSelect} />,
    );
    expect(container.querySelectorAll('tr.is-skipped')).toHaveLength(2);
    expect(screen.getByRole('button', { name: /Use row 3 as header/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Use row 1 as the header' }));
    expect(onSelect).toHaveBeenCalledWith(0);
  });
});

describe('Apple Numbers files', () => {
  it('are refused with export steps', () => {
    expect(numbersFileError([{ name: 'Balance Sheet.numbers' }])).toBe(NUMBERS_FILE_MESSAGE);
    expect(NUMBERS_FILE_MESSAGE).toContain('Alt+E');
    expect(numbersFileError([{ name: 'sales.xlsx' }])).toBe('');
  });
});
