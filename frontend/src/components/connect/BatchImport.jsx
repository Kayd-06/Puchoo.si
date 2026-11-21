import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  CircleSlash,
  FileSpreadsheet,
  LoaderCircle,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-react';
import { fetchApi } from '../../api/client';
import CompanyPicker, { NEW_COMPANY } from './CompanyPicker';
import HeaderRowPicker from './HeaderRowPicker';
import { TABLE_NAME_HINT, TallyReportBody } from './TallyImportPreview';
import '../../styles/tallyImport.css';
import '../../styles/batchImport.css';

export const MAX_BATCH_FILES = 10;
export const TOO_MANY_FILES_MESSAGE = `You can upload up to ${MAX_BATCH_FILES} files at once. Remove some files and try again.`;
const PREVIEW_CONCURRENCY = 3;
const LEDGER_TABLE = 'ledger_vouchers';

/** Same loose comparison as the server: case, spacing and a trailing FY tag. */
export function normalizeCompanyName(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/\s*\(\s*(?:fy\s*)?\d{2,4}\s*[-/]\s*\d{2,4}\s*\)\s*$/, '')
    .replace(/[ .,]+$/, '');
}

function importForm(file, fields) {
  const form = new FormData();
  form.append('file', file);
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') form.append(key, String(value));
  });
  return form;
}

function sizeLabel(file) {
  return file.size < 1024 * 1024
    ? `${Math.max(1, Math.round(file.size / 1024))} KB`
    : `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function suggestedTable(preview) {
  if (!preview) return '';
  if (preview.kind === 'tally') return preview.company?.suggested_table || preview.table || '';
  return preview.suggested_table || preview.company?.suggested_table || '';
}

/** Whether a file can be saved right now, and why not. */
export function saveBlocker(item, mismatch) {
  if (item.cancelled) return 'cancelled';
  if (item.status === 'loading') return 'loading';
  if (item.status === 'error') return 'error';
  if (!String(item.tableName || '').trim()) return 'table';
  if (mismatch && !item.confirmMismatch) return 'mismatch';
  return '';
}

/**
 * Which kept Tally files look like a different company. For an existing company
 * the server says so in the preview; for a new company the first kept Tally
 * file sets the company name and every other file is compared with it.
 */
export function mismatchedIds(items, existingTarget) {
  const kept = items.filter(
    (item) => !item.cancelled && item.status === 'ready' && item.preview?.kind === 'tally',
  );
  if (existingTarget) {
    return new Set(
      kept.filter((item) => item.preview.company?.different_company).map((item) => item.id),
    );
  }
  const reference = kept
    .map((item) => normalizeCompanyName(item.preview.company_name))
    .find((name) => name);
  if (!reference) return new Set();
  return new Set(
    kept
      .filter((item) => {
        const name = normalizeCompanyName(item.preview.company_name);
        return name && name !== reference;
      })
      .map((item) => item.id),
  );
}

/** What saving a Tally file will do, in plain words. */
function tallyDestination(item, items, existingTarget) {
  const preview = item.preview;
  if (item.tableEdited) return `Saved as the table ${item.tableName.trim()}.`;
  if (existingTarget && preview.company?.suggested_mode === 'append') {
    const skipped = preview.append?.existing_vouchers || 0;
    return `Adds rows to the existing table ${preview.company.suggested_table}${
      skipped ? `. ${plural(skipped, 'voucher')} already saved will be skipped` : ''
    }.`;
  }
  const earlier = items.find(
    (other) =>
      other.id !== item.id &&
      !other.cancelled &&
      other.status === 'ready' &&
      other.preview?.kind === 'tally' &&
      other.preview.table === preview.table &&
      other.preview.report_name === preview.report_name &&
      items.indexOf(other) < items.indexOf(item),
  );
  if (earlier && preview.table !== LEDGER_TABLE) {
    return `Same report as ${earlier.file.name}: its rows go into the same table, and vouchers already saved are skipped.`;
  }
  if (earlier) return 'A second ledger: it gets its own table so no rows are lost.';
  return `New table ${item.tableName || preview.table}.`;
}

function PlainTablePreview({ preview, headingId, fileName }) {
  const columns = preview.columns || [];
  const rows = preview.sample_rows || [];
  const extraSheets = (preview.sheets || []).length - 1;
  return (
    <>
      <header className="tally-preview-header">
        <span className="tally-preview-badge">
          <FileSpreadsheet size={14} /> Spreadsheet
        </span>
        <h2 id={headingId}>{fileName}</h2>
      </header>
      <dl className="tally-preview-facts batch-plain-facts">
        <div>
          <dt>Type</dt>
          <dd>Plain table</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{Number(preview.row_count || 0).toLocaleString('en-IN')}</dd>
        </div>
        <div>
          <dt>Columns</dt>
          <dd>{columns.length}</dd>
        </div>
      </dl>
      {extraSheets > 0 && (
        <p className="tally-preview-hint">
          This workbook has {plural(extraSheets + 1, 'sheet')}. Each sheet becomes its own table.
        </p>
      )}
      <div className="tally-preview-table-wrap" tabIndex={0} aria-label={`Rows of ${fileName}`}>
        <table className="tally-preview-table">
          <caption>
            First {rows.length} of {Number(preview.row_count || 0).toLocaleString('en-IN')} rows
          </caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column}>{row[column] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function BatchCard({
  item,
  index,
  total,
  items,
  existingTarget,
  mismatch,
  companyOnFile,
  busy,
  onChange,
}) {
  const id = `batch-file-${item.id}`;
  const preview = item.preview;
  if (item.cancelled) {
    return (
      <li className="batch-card is-cancelled" aria-label={item.file.name}>
        <div className="batch-card-bar">
          <span className="batch-card-index">
            {index + 1}/{total}
          </span>
          <strong>{item.file.name}</strong>
          <span className="batch-card-state is-cancelled">
            <CircleSlash size={13} /> Cancelled. This file will not be uploaded.
          </span>
          <button
            type="button"
            className="batch-card-undo"
            disabled={busy}
            onClick={() => onChange({ cancelled: false })}
          >
            <RotateCcw size={13} /> Undo
          </button>
        </div>
      </li>
    );
  }
  return (
    <li className="batch-card tally-preview" aria-labelledby={`${id}-title`}>
      <div className="batch-card-bar">
        <span className="batch-card-index">
          {index + 1}/{total}
        </span>
        <strong>{item.file.name}</strong>
        <span className="batch-card-size">{sizeLabel(item.file)}</span>
        <button
          type="button"
          className="batch-card-remove"
          disabled={busy}
          aria-label={`Cancel ${item.file.name}`}
          onClick={() => onChange({ cancelled: true })}
        >
          <X size={13} /> Cancel file
        </button>
      </div>

      {item.status === 'loading' && (
        <p className="batch-card-loading">
          <LoaderCircle size={15} className="voice-spinner" aria-hidden="true" /> Checking this
          file…
        </p>
      )}
      {item.status === 'error' && (
        <div className="batch-card-error" role="alert">
          <AlertTriangle size={15} />
          <span>
            This file can&apos;t be saved: {item.error} It will be left out unless you fix it and
            add it again.
          </span>
        </div>
      )}
      {item.status === 'ready' && preview.kind === 'tally' && (
        <TallyReportBody
          preview={preview}
          fileName={item.file.name}
          tableName={item.tableName}
          headingId={`${id}-title`}
        />
      )}
      {item.status === 'ready' && preview.kind === 'unknown' && (
        <HeaderRowPicker
          embedded
          idPrefix={id}
          fileName={item.file.name}
          rows={preview.rows}
          selectedRow={item.headerRow}
          onSelect={(row) => onChange({ headerRow: row })}
        />
      )}
      {item.status === 'ready' && preview.kind === 'table' && (
        <PlainTablePreview preview={preview} fileName={item.file.name} headingId={`${id}-title`} />
      )}

      {item.status === 'ready' && (
        <div className="batch-card-destination">
          <label className="tally-preview-field" htmlFor={`${id}-table`}>
            <span>Table name</span>
            <input
              id={`${id}-table`}
              value={item.tableName}
              maxLength={63}
              disabled={busy}
              onChange={(event) => onChange({ tableName: event.target.value, tableEdited: true })}
            />
          </label>
          <p className="tally-preview-hint">
            {preview.kind === 'tally'
              ? tallyDestination(item, items, existingTarget)
              : preview.kind === 'unknown'
                ? `Row ${item.headerRow + 1} is used as the column names. Rows above it are left out.`
                : 'Saved as a new table. Nothing already in the company is changed.'}
          </p>
        </div>
      )}

      {mismatch && (
        <label className="tally-preview-mismatch batch-card-mismatch" role="alert">
          <AlertTriangle size={16} />
          <span>
            <strong>This report looks like it is from a different company.</strong>
            <span className="batch-card-mismatch-copy">
              The header says {preview.company_name || 'another company'}, but this company holds
              reports from {companyOnFile || 'another company'}. It is only saved if you tick this
              box.
            </span>
            <span className="batch-card-mismatch-check">
              <input
                type="checkbox"
                checked={Boolean(item.confirmMismatch)}
                disabled={busy}
                onChange={(event) => onChange({ confirmMismatch: event.target.checked })}
              />
              I checked this. Add this file anyway.
            </span>
          </span>
        </label>
      )}
    </li>
  );
}

function ResultRow({ item, result, onConfirmAnyway, busy }) {
  if (!result) {
    return (
      <li className="batch-result is-cancelled">
        <CircleSlash size={15} />
        <div>
          <strong>{item.file.name}</strong>
          <span>Not uploaded (cancelled). You can add it later.</span>
        </div>
      </li>
    );
  }
  if (result.status === 'added') {
    const summary = result.summary || {};
    const tables = summary.tables?.length > 1 ? summary.tables.join(', ') : summary.table;
    const rows =
      summary.kind === 'tally'
        ? `${plural(summary.rows_added || 0, 'row')} added${
            summary.rows_skipped ? `, ${summary.rows_skipped} already saved and skipped` : ''
          }`
        : summary.rows !== undefined
          ? `${plural(summary.rows, 'row')} saved`
          : 'Saved';
    return (
      <li className="batch-result is-added">
        <Check size={15} />
        <div>
          <strong>{item.file.name}</strong>
          <span>
            {rows} in the table <code>{tables}</code>.
          </span>
        </div>
      </li>
    );
  }
  return (
    <li className={`batch-result is-${result.status}`}>
      <AlertTriangle size={15} />
      <div>
        <strong>{item.file.name}</strong>
        <span>
          {result.status === 'needs_confirmation' ? 'Not saved yet: ' : 'Not saved: '}
          {result.reason}
        </span>
      </div>
      {result.status === 'needs_confirmation' && (
        <button
          type="button"
          className="batch-card-undo"
          disabled={busy}
          onClick={() => onConfirmAnyway(item)}
        >
          Add this file anyway
        </button>
      )}
    </li>
  );
}

export default function BatchImport({
  files,
  companies,
  initialTarget = NEW_COMPANY,
  onCompanySaved,
  onClose,
  onAddMore,
}) {
  const nextId = useRef(0);
  const [items, setItems] = useState(() =>
    files.map((file) => ({
      id: (nextId.current += 1),
      file,
      status: 'loading',
      preview: null,
      error: '',
      cancelled: false,
      tableName: '',
      tableEdited: false,
      headerRow: 0,
      confirmMismatch: false,
    })),
  );
  const [target, setTarget] = useState(initialTarget);
  const [newName, setNewName] = useState('');
  const [nameEdited, setNameEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState(null);
  const [savedCompany, setSavedCompany] = useState(null);
  const [error, setError] = useState('');
  const targetCompany = companies.find((company) => company.id === target) || null;
  const existingTarget = targetCompany ? targetCompany.id : '';

  // Preview every file with the chosen company, a few at a time. Edits and
  // cancellations are kept when the company changes; only previews refresh.
  useEffect(() => {
    let live = true;
    const queue = files.map((file, position) => ({ file, position }));
    const run = async () => {
      while (live && queue.length) {
        const { file, position } = queue.shift();
        try {
          const preview = await fetchApi('/workspaces/import/preview', {
            method: 'POST',
            body: importForm(file, { target_workspace_id: existingTarget }),
          });
          if (!live) return;
          setItems((current) =>
            current.map((item, index) =>
              index === position
                ? {
                    ...item,
                    status: 'ready',
                    preview,
                    error: '',
                    tableName: item.tableEdited ? item.tableName : suggestedTable(preview),
                    headerRow:
                      item.status === 'ready' ? item.headerRow : preview.suggested_header_row || 0,
                  }
                : item,
            ),
          );
        } catch (failure) {
          if (!live) return;
          setItems((current) =>
            current.map((item, index) =>
              index === position
                ? {
                    ...item,
                    status: 'error',
                    preview: null,
                    error: failure.message || 'The file could not be read.',
                  }
                : item,
            ),
          );
        }
      }
    };
    Promise.all(Array.from({ length: PREVIEW_CONCURRENCY }, run));
    return () => {
      live = false;
    };
  }, [files, existingTarget]);

  const mismatches = useMemo(() => mismatchedIds(items, existingTarget), [items, existingTarget]);
  const firstTally = items.find(
    (item) => !item.cancelled && item.status === 'ready' && item.preview?.kind === 'tally',
  );
  const companyOnFile = existingTarget
    ? firstTally?.preview?.company?.company_on_file || targetCompany?.name
    : firstTally?.preview?.company_name;
  const suggestedName = firstTally?.preview?.company_name || '';
  const companyName = nameEdited ? newName : newName || suggestedName;
  const blockers = items.map((item) => saveBlocker(item, mismatches.has(item.id)));
  const saveable = items.filter((_, index) => !blockers[index]);
  const cancelled = items.filter((item) => item.cancelled).length;
  const loading = items.some((item) => !item.cancelled && item.status === 'loading');
  const waiting = blockers.filter((blocker) => blocker === 'mismatch').length;
  const unreadable = blockers.filter((blocker) => blocker === 'error').length;
  const needsName = !existingTarget && !companyName.trim();
  const destinationName = targetCompany?.name || companyName.trim() || 'the new company';

  const update = (id, patch) =>
    setItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));

  function fieldsFor(item, companyId) {
    const preview = item.preview;
    const fields = companyId ? { target_workspace_id: companyId } : { name: companyName.trim() };
    if (preview.kind === 'tally') {
      if (companyId) fields.mode = 'auto';
      if (item.tableEdited) fields.table_name = item.tableName.trim();
      // Every ledger export is called "Ledger Vouchers"; the file name tells them apart
      // in the files list. Labels stay on the server and are never sent to the AI.
      if (preview.table === LEDGER_TABLE) fields.label = item.file.name.replace(/\.[^.]+$/, '');
      // Every ledger export is called "Ledger Vouchers"; the file name tells them apart
      // in the files list. Labels stay on the server and are never sent to the AI.
      if (preview.table === LEDGER_TABLE) fields.label = item.file.name.replace(/\.[^.]+$/, '');
    } else {
      fields.table_name = item.tableName.trim();
    }
    if (preview.kind === 'unknown') fields.header_row = item.headerRow;
    if (item.confirmMismatch) fields.confirm_company_mismatch = 'true';
    return fields;
  }

  async function saveOne(item, companyId, extra = {}) {
    try {
      const response = await fetchApi('/workspaces/import/confirm', {
        method: 'POST',
        body: importForm(item.file, { ...fieldsFor(item, companyId), ...extra }),
      });
      return { status: 'added', summary: response.import, workspace: response.workspace };
    } catch (failure) {
      if (failure.code === 'company_mismatch') {
        return { status: 'needs_confirmation', reason: failure.message };
      }
      return { status: 'failed', reason: failure.message || 'The file could not be saved.' };
    }
  }

  async function save() {
    if (saving || loading || !saveable.length || needsName) return;
    setSaving(true);
    setError('');
    setProgress(0);
    let companyId = existingTarget;
    let company = targetCompany;
    const outcome = {};
    for (const item of items) {
      const blocker = blockers[items.indexOf(item)];
      if (blocker === 'cancelled') continue;
      if (blocker === 'mismatch') {
        outcome[item.id] = {
          status: 'needs_confirmation',
          reason: 'it looks like a different company and the box was not ticked.',
        };
        continue;
      }
      if (blocker) {
        outcome[item.id] = {
          status: 'failed',
          reason:
            blocker === 'error' ? item.error : 'it has no table name. Add it again with a name.',
        };
        continue;
      }
      // Each file is its own add: one failure never undoes or blocks the others.
      const result = await saveOne(item, companyId);
      outcome[item.id] = result;
      if (result.status === 'added') {
        const created = !companyId;
        companyId = result.workspace.id;
        company = result.workspace;
        onCompanySaved?.(result.workspace, !created);
      }
      setProgress((count) => count + 1);
    }
    setResults(outcome);
    setSavedCompany(company);
    if (!companyId) setError('No file was saved, so no company was created.');
    setSaving(false);
  }

  async function confirmAnyway(item) {
    if (!savedCompany?.id) return;
    setSaving(true);
    const result = await saveOne({ ...item, confirmMismatch: true }, savedCompany.id, {
      confirm_company_mismatch: 'true',
    });
    if (result.status === 'added') onCompanySaved?.(result.workspace, true);
    setResults((current) => ({ ...current, [item.id]: result }));
    setSaving(false);
  }

  if (results) {
    const values = Object.values(results);
    const added = values.filter((result) => result.status === 'added').length;
    const notSaved = values.length - added;
    return (
      <section className="batch-results" aria-labelledby="batch-results-title">
        <span className="tally-preview-badge">
          <Check size={14} /> Upload finished
        </span>
        <h2 id="batch-results-title">
          {savedCompany
            ? `${plural(added, 'file')} added to ${savedCompany.name}`
            : 'No files were saved'}
        </h2>
        <p className="batch-results-summary" role="status">
          {plural(added, 'file')} added
          {notSaved ? `, ${notSaved} not saved` : ''}
          {cancelled ? `, ${cancelled} cancelled` : ''}. Files already in the company were not
          changed.
        </p>
        {error && (
          <p className="connect-error" role="alert">
            {error}
          </p>
        )}
        <ul className="batch-result-list">
          {items.map((item) => (
            <ResultRow
              key={item.id}
              item={item}
              result={results[item.id]}
              busy={saving}
              onConfirmAnyway={confirmAnyway}
            />
          ))}
        </ul>
        <div className="batch-results-actions">
          {savedCompany && (
            <Link to="/ask" className="ui-action ui-action-primary">
              Ask your data <ArrowUpRight size={17} />
            </Link>
          )}
          {savedCompany && (
            <button
              type="button"
              className="tally-preview-cancel"
              onClick={() => onAddMore?.(savedCompany.id)}
            >
              Add more files to {savedCompany.name}
            </button>
          )}
          {!savedCompany && (
            <button type="button" className="tally-preview-cancel" onClick={onClose}>
              Start again
            </button>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className="batch-import" aria-labelledby="batch-import-title">
      <header className="batch-import-header">
        <span className="tally-preview-badge">
          <FileSpreadsheet size={14} /> {plural(items.length, 'file')} selected
        </span>
        <h2 id="batch-import-title">Check each file before saving</h2>
        <p>
          Every file gets its own preview. Cancel any file you don&apos;t want; the rest are saved
          into one company. Nothing is saved until you press Save.
        </p>
      </header>
      <fieldset className="tally-preview-destination batch-company" disabled={saving}>
        <legend>Company for these files</legend>
        <CompanyPicker
          idPrefix="batch-company"
          companies={companies}
          value={target}
          onChange={setTarget}
          newName={companyName}
          onNewNameChange={(value) => {
            setNewName(value);
            setNameEdited(true);
          }}
          newNamePlaceholder={suggestedName || 'Company name, e.g. Demo Traders'}
        />
        <p className="tally-preview-hint">
          {existingTarget
            ? `Files are added next to the ones already in ${targetCompany.name}. Nothing there is deleted or replaced.`
            : 'The first file creates the company and every other file goes into it.'}{' '}
          {TABLE_NAME_HINT}
        </p>
      </fieldset>

      <ol className="batch-card-list">
        {items.map((item, index) => (
          <BatchCard
            key={item.id}
            item={item}
            index={index}
            total={items.length}
            items={items}
            existingTarget={existingTarget}
            mismatch={mismatches.has(item.id)}
            companyOnFile={companyOnFile}
            busy={saving}
            onChange={(patch) => update(item.id, patch)}
          />
        ))}
      </ol>

      <div className="batch-import-footer">
        <p role="status">
          {loading
            ? 'Checking files…'
            : saving
              ? `Saving ${progress + 1} of ${saveable.length}…`
              : `${plural(saveable.length, 'file')} will be saved to ${destinationName}.`}
          {cancelled ? ` ${cancelled} cancelled.` : ''}
          {waiting ? ` ${waiting} waiting for your confirmation.` : ''}
          {unreadable ? ` ${unreadable} can't be read.` : ''}
        </p>
        {error && (
          <p className="connect-error" role="alert">
            {error}
          </p>
        )}
        <div className="tally-preview-actions">
          <button
            type="button"
            className="tally-preview-cancel"
            onClick={onClose}
            disabled={saving}
          >
            <Trash2 size={15} /> Cancel all
          </button>
          <button
            type="button"
            className="connect-submit"
            onClick={save}
            disabled={saving || loading || !saveable.length || needsName}
          >
            <Check size={15} />{' '}
            {saving
              ? 'Saving…'
              : `Save ${plural(saveable.length, 'file')} to ${targetCompany ? targetCompany.name : 'a new company'}`}
          </button>
        </div>
      </div>
    </section>
  );
}
