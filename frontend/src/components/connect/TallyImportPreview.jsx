import { AlertTriangle, Check, FileSpreadsheet, X } from 'lucide-react';
import '../../styles/tallyImport.css';
import CompanyPicker, { NEW_COMPANY } from './CompanyPicker';

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

export const TABLE_NAME_HINT =
  'Table names are sent to the AI with your questions, so keep company or party names out of them.';

function AppendNote({ info }) {
  if (!info) return null;
  return (
    <p className="tally-preview-append" role="note">
      {info.existing_vouchers > 0
        ? `${info.existing_vouchers} voucher${info.existing_vouchers === 1 ? ' is' : 's are'} already saved and will be skipped. `
        : ''}
      {info.new_rows} new row{info.new_rows === 1 ? '' : 's'} will be added.
    </p>
  );
}

/** The checked contents of one Tally report: type, period, rows, totals and first rows. */
export function TallyReportBody({
  preview,
  fileName,
  tableName,
  headingId = 'tally-preview-title',
}) {
  const columns = preview.columns.filter((column) => !HIDDEN_PREVIEW_COLUMNS.has(column));
  const status = preview.totals_check?.status || 'unavailable';
  const failedChecks = (preview.totals_check?.checks || []).filter((check) => !check.ok);
  return (
    <>
      <header className="tally-preview-header">
        <span className="tally-preview-badge">
          <FileSpreadsheet size={14} /> Tally report detected
        </span>
        <h2 id={headingId}>{preview.report_name}</h2>
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
            <code>{tableName || preview.table}</code>
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
    </>
  );
}

export default function TallyImportPreview({
  preview,
  fileName,
  companies = [],
  targetId = NEW_COMPANY,
  onTargetChange,
  newCompanyName = '',
  onNewCompanyNameChange,
  choice = null,
  onChoiceChange,
  appendInfo = null,
  mismatchConfirmed = false,
  onMismatchConfirmedChange,
  onConfirm,
  onCancel,
  busy = false,
}) {
  const company = preview.company || {};
  const existing = targetId && targetId !== NEW_COMPANY;
  const targetName = companies.find((item) => item.id === targetId)?.name || 'this company';
  const candidates = existing ? company.append_candidates || [] : [];
  const mode = choice?.mode || company.suggested_mode || 'new';
  const tableName = choice?.table ?? company.suggested_table ?? preview.table;
  const mismatch = existing && company.different_company;
  const update = (patch) =>
    onChoiceChange?.({ mode, table: tableName, label: choice?.label || '', ...patch });
  const blocked = busy || (mismatch && !mismatchConfirmed) || !String(tableName || '').trim();
  return (
    <section className="tally-preview" aria-labelledby="tally-preview-title">
      <TallyReportBody
        preview={preview}
        fileName={fileName}
        tableName={mode === 'append' ? tableName : tableName || preview.table}
      />

      <fieldset className="tally-preview-destination" disabled={busy}>
        <legend>Where this file goes</legend>
        <CompanyPicker
          idPrefix="tally-company"
          companies={companies}
          value={targetId}
          onChange={onTargetChange}
          newName={newCompanyName}
          onNewNameChange={onNewCompanyNameChange}
          newNamePlaceholder={preview.company_name || 'Company name'}
        />
        {candidates.length > 0 && (
          <div className="tally-preview-modes" role="radiogroup" aria-label="Table">
            <label>
              <input
                type="radio"
                name="tally-mode"
                checked={mode === 'append'}
                onChange={() => update({ mode: 'append', table: candidates[0] })}
              />
              <span>Add rows to an existing table (same report, new period)</span>
            </label>
            <label>
              <input
                type="radio"
                name="tally-mode"
                checked={mode === 'new'}
                onChange={() =>
                  update({
                    mode: 'new',
                    table:
                      company.suggested_mode === 'new' ? company.suggested_table : preview.table,
                  })
                }
              />
              <span>Save as a new table</span>
            </label>
          </div>
        )}
        {mode === 'append' && candidates.length > 0 ? (
          <label className="tally-preview-field" htmlFor="tally-append-table">
            <span>Existing table</span>
            <select
              id="tally-append-table"
              value={tableName}
              onChange={(event) => update({ table: event.target.value })}
            >
              {candidates.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label className="tally-preview-field" htmlFor="tally-table-name">
              <span>Table name</span>
              <input
                id="tally-table-name"
                value={tableName}
                maxLength={63}
                onChange={(event) => update({ table: event.target.value })}
              />
            </label>
            <label className="tally-preview-field" htmlFor="tally-file-label">
              <span>Name in the files list (optional)</span>
              <input
                id="tally-file-label"
                value={choice?.label || ''}
                maxLength={120}
                placeholder={preview.report_name}
                onChange={(event) => update({ label: event.target.value })}
              />
            </label>
            <p className="tally-preview-hint">{TABLE_NAME_HINT}</p>
          </>
        )}
      </fieldset>
      {existing && mode === 'append' && <AppendNote info={appendInfo || preview.append} />}

      {mismatch && (
        <div className="tally-preview-mismatch" role="alert">
          <AlertTriangle size={16} />
          <div>
            <strong>This report looks like it is from a different company.</strong>
            <p>
              The report header says {preview.company_name || 'another company'}, but {targetName}{' '}
              holds reports from {company.company_on_file || 'another company'}.
            </p>
            <label>
              <input
                type="checkbox"
                checked={mismatchConfirmed}
                onChange={(event) => onMismatchConfirmedChange?.(event.target.checked)}
              />
              <span>I checked this. Add it to {targetName} anyway.</span>
            </label>
          </div>
        </div>
      )}

      <div className="tally-preview-actions">
        <button type="button" className="tally-preview-cancel" onClick={onCancel} disabled={busy}>
          <X size={15} /> Cancel
        </button>
        <button type="button" className="connect-submit" onClick={onConfirm} disabled={blocked}>
          <Check size={15} />{' '}
          {busy ? 'Saving…' : existing ? `Add to ${targetName}` : 'Create company and save'}
        </button>
      </div>
    </section>
  );
}
