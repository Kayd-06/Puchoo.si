import { AlertTriangle, Check, FileSpreadsheet, X } from 'lucide-react';
import '../../styles/tallyImport.css';

const REPORT_LABELS = {
  voucher_register: 'Voucher register',
  balance_sheet: 'Balance sheet',
  profit_and_loss: 'Profit & loss',
  trial_balance: 'Trial balance',
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatReportDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  if (!match) return value || '';
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

export function reportPeriod(preview) {
  if (preview.period_start && preview.period_end) {
    return `${formatReportDate(preview.period_start)} to ${formatReportDate(preview.period_end)}`;
  }
  if (preview.report_date) return `As at ${formatReportDate(preview.report_date)}`;
  return 'Period not found';
}

function formatCell(value) {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'number') {
    return value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return String(value);
}

export function totalsSummary(totals) {
  const checks = totals?.checks || [];
  if (totals?.status === 'pass') {
    return `Totals match Tally (${checks.length} check${checks.length === 1 ? '' : 's'})`;
  }
  if (totals?.status === 'fail') {
    const failed = checks.filter((check) => !check.ok).length;
    return `${failed} total${failed === 1 ? '' : 's'} do not match Tally`;
  }
  return 'No Tally total to check against';
}

const HIDDEN_PREVIEW_COLUMNS = new Set(['period_start', 'period_end', 'source_report', 'line_no']);

export default function TallyImportPreview({
  preview,
  fileName,
  tallySources = [],
  targetId = '',
  onTargetChange,
  onConfirm,
  onCancel,
  busy = false,
}) {
  const columns = preview.columns.filter((column) => !HIDDEN_PREVIEW_COLUMNS.has(column));
  const status = preview.totals_check?.status || 'unavailable';
  const failedChecks = (preview.totals_check?.checks || []).filter((check) => !check.ok);
  const append = preview.append;
  return (
    <section className="tally-preview" aria-labelledby="tally-preview-title">
      <header className="tally-preview-header">
        <span className="tally-preview-badge">
          <FileSpreadsheet size={14} /> Tally report detected
        </span>
        <h2 id="tally-preview-title">{preview.report_name}</h2>
        <p className="tally-preview-file">{fileName}</p>
      </header>

      <dl className="tally-preview-facts">
        <div>
          <dt>Type</dt>
          <dd>{REPORT_LABELS[preview.report_type] || preview.report_type}</dd>
        </div>
        <div>
          <dt>Period</dt>
          <dd>{reportPeriod(preview)}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{preview.row_count.toLocaleString('en-IN')}</dd>
        </div>
        <div>
          <dt>Table</dt>
          <dd>
            <code>{preview.table}</code>
          </dd>
        </div>
      </dl>

      <p className="tally-preview-private">
        The company name, address, phone and email from the report header stay out of the table and
        are never sent to the AI.
      </p>

      <div className={`tally-preview-totals is-${status}`} role="status">
        {status === 'pass' ? <Check size={16} /> : <AlertTriangle size={16} />}
        <span>{totalsSummary(preview.totals_check)}</span>
      </div>
      {failedChecks.length > 0 && (
        <ul className="tally-preview-failed">
          {failedChecks.map((check) => (
            <li key={check.label}>
              {check.label}: rows {formatCell(check.actual)}, Tally {formatCell(check.expected)}
            </li>
          ))}
        </ul>
      )}

      {preview.warnings.length > 0 && (
        <div className="tally-preview-warnings">
          <strong>Check before saving</strong>
          <ul>
            {preview.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="tally-preview-table-wrap" tabIndex={0} aria-label="Cleaned rows preview">
        <table className="tally-preview-table">
          <caption>
            First {preview.sample_rows.length} of {preview.row_count.toLocaleString('en-IN')}{' '}
            cleaned rows
          </caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className={
                    preview.sample_rows.some((row) => typeof row[column] === 'number')
                      ? 'is-number'
                      : ''
                  }
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.sample_rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column} className={typeof row[column] === 'number' ? 'is-number' : ''}>
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {tallySources.length > 0 && (
        <label className="tally-preview-target">
          <span>Save to</span>
          <select
            value={targetId}
            onChange={(event) => onTargetChange?.(event.target.value)}
            disabled={busy}
          >
            <option value="">A new data source</option>
            {tallySources.map((source) => (
              <option key={source.id} value={source.id}>
                Add to {source.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {append && (
        <p className="tally-preview-append" role="note">
          {append.existing_vouchers > 0
            ? `${append.existing_vouchers} voucher${append.existing_vouchers === 1 ? ' is' : 's are'} already saved and will be skipped. `
            : ''}
          {append.new_rows} new row{append.new_rows === 1 ? '' : 's'} will be added.
          {append.different_company
            ? ' This report is from a different company than the saved data.'
            : ''}
        </p>
      )}

      <div className="tally-preview-actions">
        <button type="button" className="tally-preview-cancel" onClick={onCancel} disabled={busy}>
          <X size={15} /> Cancel
        </button>
        <button type="button" className="connect-submit" onClick={onConfirm} disabled={busy}>
          <Check size={15} /> {busy ? 'Saving…' : 'Confirm and save'}
        </button>
      </div>
    </section>
  );
}
