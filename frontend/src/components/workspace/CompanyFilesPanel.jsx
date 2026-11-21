import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRightLeft, FileSpreadsheet, RefreshCw, Trash2 } from 'lucide-react';
import { fetchApi } from '../../api/client';
import { useAppContext } from '../../context/AppContext';
import { useAuth } from '../../context/AuthContext';
import { can } from '../../auth/capabilities';
import { chatScope, invalidateHistory } from '../../hooks/useChatState';
import { formatReportDate } from '../connect/TallyImportPreview';
import { companyOptions, fileCountLabel } from '../connect/CompanyPicker';
import './companyFiles.css';

const KIND_LABELS = { tally: 'Tally report', table: 'Spreadsheet' };

export function filePeriod(file) {
  if (file.period_start && file.period_end) {
    return `${formatReportDate(file.period_start)} to ${formatReportDate(file.period_end)}`;
  }
  if (file.report_date) return `As at ${formatReportDate(file.report_date)}`;
  return 'No period in file';
}

export function addedDate(value) {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not recorded';
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function FileRow({ file, canManage, busy, onRemove, onReplace }) {
  const [confirming, setConfirming] = useState(false);
  const input = useRef(null);
  return (
    <li className="company-file">
      <div className="company-file-icon" aria-hidden="true">
        <FileSpreadsheet size={16} />
      </div>
      <div className="company-file-copy">
        <strong>{file.label}</strong>
        <span>
          <code>{file.table}</code> · {KIND_LABELS[file.kind] || 'File'}
        </span>
      </div>
      <dl className="company-file-facts">
        <div>
          <dt>Rows</dt>
          <dd>{Number(file.rows || 0).toLocaleString('en-IN')}</dd>
        </div>
        <div>
          <dt>Period</dt>
          <dd>{filePeriod(file)}</dd>
        </div>
        <div>
          <dt>Added</dt>
          <dd>{addedDate(file.added_at)}</dd>
        </div>
      </dl>
      {canManage && (
        <div className="company-file-actions">
          <input
            ref={input}
            type="file"
            accept=".csv,.xlsx"
            hidden
            aria-label={`Replace file for ${file.label}`}
            onChange={(event) => {
              const [chosen] = event.target.files || [];
              event.target.value = '';
              if (chosen) onReplace(file, chosen);
            }}
          />
          {!confirming ? (
            <>
              <button
                type="button"
                className="settings-action"
                disabled={busy}
                onClick={() => input.current?.click()}
              >
                <RefreshCw size={13} /> Replace
              </button>
              <button
                type="button"
                className="company-file-remove"
                disabled={busy}
                onClick={() => setConfirming(true)}
              >
                <Trash2 size={13} /> Remove
              </button>
            </>
          ) : (
            <div className="company-file-confirm" role="group" aria-label={`Remove ${file.label}`}>
              <span>Remove {file.label}? Its rows are deleted from this company.</span>
              <button
                type="button"
                className="company-file-remove"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onRemove(file);
                }}
              >
                Remove file
              </button>
              <button
                type="button"
                className="settings-action"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                Keep
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function MergeSection({ target, sources, onMerged }) {
  const { user } = useAuth();
  const [sourceId, setSourceId] = useState('');
  const [preview, setPreview] = useState(null);
  const [needsConfirm, setNeedsConfirm] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const source = sources.find((item) => item.id === sourceId) || null;

  async function choose(id) {
    setSourceId(id);
    setPreview(null);
    setNeedsConfirm(false);
    setConfirmed(false);
    setError('');
    if (!id) return;
    try {
      const result = await fetchApi(
        `/workspaces/${encodeURIComponent(target.id)}/merge-preview?source_workspace_id=${encodeURIComponent(id)}`,
      );
      setPreview(result);
      setNeedsConfirm(Boolean(result.different_company));
    } catch (failure) {
      setError(failure.message || 'Could not check that data source.');
    }
  }

  async function merge() {
    if (!source || busy || (needsConfirm && !confirmed)) return;
    setBusy(true);
    setError('');
    try {
      const result = await fetchApi(`/workspaces/${encodeURIComponent(target.id)}/merge`, {
        method: 'POST',
        body: JSON.stringify({
          source_workspace_id: source.id,
          confirm_company_mismatch: confirmed,
        }),
      });
      invalidateHistory(chatScope(user, source.id));
      invalidateHistory(chatScope(user, target.id));
      onMerged(result, source);
      setSourceId('');
      setPreview(null);
      setConfirmed(false);
    } catch (failure) {
      if (failure.code === 'company_mismatch') setNeedsConfirm(true);
      setError(failure.message || 'Could not move that data source.');
    } finally {
      setBusy(false);
    }
  }

  const renamed = Object.entries(preview?.renamed || {});
  return (
    <div className="company-merge">
      <div className="company-merge-copy">
        <span className="settings-section-kicker">Move into company</span>
        <h3>Combine another data source into {target.name}</h3>
        <p>
          Its files become tables in {target.name} and its question history comes along. The old
          entry is then removed from the list.
        </p>
      </div>
      <label htmlFor="company-merge-source">
        <span>Data source to move</span>
        <select
          id="company-merge-source"
          value={sourceId}
          disabled={busy}
          onChange={(event) => choose(event.target.value)}
        >
          <option value="">Choose a data source</option>
          {sources.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
              {typeof item.file_count === 'number' ? ` (${fileCountLabel(item.file_count)})` : ''}
            </option>
          ))}
        </select>
      </label>
      {preview && (
        <p className="company-merge-plan" role="note">
          {Object.keys(preview.tables || {}).length} table
          {Object.keys(preview.tables || {}).length === 1 ? '' : 's'} will move.
          {renamed.length > 0 &&
            ` Renamed to avoid a clash: ${renamed.map(([from, to]) => `${from} → ${to}`).join(', ')}.`}
        </p>
      )}
      {needsConfirm && source && (
        <label className="company-merge-warning" role="alert">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>
            {source.name} looks like it is from a different company. I checked this, move it anyway.
          </span>
        </label>
      )}
      {error && (
        <p className="company-files-error" role="alert">
          {error}
        </p>
      )}
      <button
        type="button"
        className="settings-action company-merge-button"
        disabled={!source || busy || (needsConfirm && !confirmed)}
        onClick={merge}
      >
        <ArrowRightLeft size={14} /> {busy ? 'Moving…' : `Move into ${target.name}`}
      </button>
    </div>
  );
}

export default function CompanyFilesPanel() {
  const { user } = useAuth();
  const { activeWorkspace, workspaces = [], updateWorkspace, removeWorkspace } = useAppContext();
  const [files, setFiles] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(null);
  const canManage = can(user, 'manage_data_sources');
  const workspaceId = activeWorkspace?.id;
  const isUpload =
    activeWorkspace &&
    activeWorkspace.source_type !== 'server' &&
    activeWorkspace.availability !== 'unavailable';

  const load = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const result = await fetchApi(`/workspaces/${encodeURIComponent(workspaceId)}/files`);
      setFiles(result.files || []);
      setError('');
    } catch (failure) {
      setFiles([]);
      setError(failure.message || 'Could not load the files in this data source.');
    }
  }, [workspaceId]);

  useEffect(() => {
    if (!isUpload) return undefined;
    let live = true;
    fetchApi(`/workspaces/${encodeURIComponent(workspaceId)}/files`)
      .then((result) => live && setFiles(result.files || []))
      .catch((failure) => {
        if (!live) return;
        setFiles([]);
        setError(failure.message || 'Could not load the files in this data source.');
      });
    return () => {
      live = false;
    };
  }, [isUpload, workspaceId]);

  if (!isUpload) return null;

  async function remove(file) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await fetchApi(
        `/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(file.table)}`,
        { method: 'DELETE' },
      );
      updateWorkspace(result.workspace);
      setMessage(`${file.label} was removed. The other files are unchanged.`);
      await load();
    } catch (failure) {
      setError(failure.message || 'Could not remove that file.');
    } finally {
      setBusy(false);
    }
  }

  async function replace(file, chosen, extra = {}) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const form = new FormData();
      form.append('file', chosen);
      Object.entries(extra).forEach(([key, value]) => form.append(key, String(value)));
      const result = await fetchApi(
        `/workspaces/${encodeURIComponent(workspaceId)}/files/${encodeURIComponent(file.table)}/replace`,
        { method: 'POST', body: form },
      );
      if (result.needs_header_row) {
        setPending({ file, chosen, kind: 'header', row: result.suggested_header_row || 0 });
        return;
      }
      setPending(null);
      updateWorkspace(result.workspace);
      setMessage(`${file.label} was replaced with ${chosen.name}.`);
      await load();
    } catch (failure) {
      if (failure.code === 'company_mismatch') {
        setPending({ file, chosen, kind: 'mismatch', message: failure.message });
      } else {
        setError(failure.message || 'Could not replace that file.');
      }
    } finally {
      setBusy(false);
    }
  }

  function merged(result, source) {
    removeWorkspace(source.id);
    updateWorkspace(result.workspace);
    const history = result.history_moved
      ? ` ${result.history_moved} earlier question${result.history_moved === 1 ? ' was' : 's were'} kept in this company's history.`
      : '';
    setMessage(`${source.name} was moved into ${activeWorkspace.name}.${history}`);
    load();
  }

  const others = companyOptions(workspaces).filter((item) => item.id !== workspaceId);
  return (
    <section className="settings-section company-files-panel" aria-labelledby="company-files-title">
      <header className="company-files-header">
        <div>
          <span className="settings-section-kicker">Company files</span>
          <h2 id="company-files-title">Files in {activeWorkspace.name}</h2>
          <p>
            Every file is a table in this company, so one question can use all of them.
            {files ? ` ${fileCountLabel(files.length)}.` : ''}
          </p>
        </div>
      </header>
      {!files && <p className="company-files-status">Loading files…</p>}
      {files && files.length > 0 && (
        <ul className="company-file-list">
          {files.map((file) => (
            <FileRow
              key={file.table}
              file={file}
              canManage={canManage}
              busy={busy}
              onRemove={remove}
              onReplace={replace}
            />
          ))}
        </ul>
      )}
      {pending?.kind === 'mismatch' && (
        <div className="company-files-warning" role="alert">
          <p>{pending.message}</p>
          <button
            type="button"
            className="settings-action"
            disabled={busy}
            onClick={() =>
              replace(pending.file, pending.chosen, { confirm_company_mismatch: 'true' })
            }
          >
            Use it anyway
          </button>
          <button
            type="button"
            className="settings-action"
            disabled={busy}
            onClick={() => setPending(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {pending?.kind === 'header' && (
        <div className="company-files-warning" role="alert">
          <p>
            {pending.chosen.name} has title rows above the table. Row {pending.row + 1} looks like
            the column names.
          </p>
          <button
            type="button"
            className="settings-action"
            disabled={busy}
            onClick={() => replace(pending.file, pending.chosen, { header_row: pending.row })}
          >
            Use row {pending.row + 1}
          </button>
          <button
            type="button"
            className="settings-action"
            disabled={busy}
            onClick={() => setPending(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {message && (
        <p className="company-files-status" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="company-files-error" role="alert">
          {error}
        </p>
      )}
      {canManage && others.length > 0 && (
        <MergeSection
          key={workspaceId}
          target={activeWorkspace}
          sources={others}
          onMerged={merged}
        />
      )}
    </section>
  );
}
