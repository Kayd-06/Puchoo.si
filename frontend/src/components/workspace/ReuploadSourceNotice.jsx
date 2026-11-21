import { useRef, useState } from 'react';
import { FileUp, LoaderCircle, TriangleAlert } from 'lucide-react';
import { fetchApi } from '../../api/client';
import './reuploadSource.css';

export const NEEDS_REUPLOAD_LABEL = 'Needs re-upload';

export function needsReupload(source) {
  return source?.availability === 'unavailable';
}

// Shown on the Ask page instead of the question box when the server no
// longer has the file behind the selected source.
export default function ReuploadSourceNotice({ source, canUpload, onReuploaded }) {
  const input = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const isServer = source.source_type === 'server';
  const isTally = source.source_type === 'tally_report';

  async function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || uploading) return;
    setUploading(true);
    setError('');
    try {
      const body = new FormData();
      body.append('file', file);
      const workspace = await fetchApi(`/workspaces/${encodeURIComponent(source.id)}/reupload`, {
        method: 'POST',
        body,
      });
      onReuploaded?.(workspace);
    } catch (failure) {
      setError(
        failure.code === 'company_has_files'
          ? `${failure.message} Reload the page to see it.`
          : failure.message || 'The file could not be uploaded. Please try again.',
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="reupload-notice" aria-labelledby="reupload-notice-title">
      <div className="reupload-notice-icon" aria-hidden="true">
        <TriangleAlert size={18} />
      </div>
      <div className="reupload-notice-copy">
        <span className="reupload-notice-tag">{NEEDS_REUPLOAD_LABEL}</span>
        <h2 id="reupload-notice-title">{source.name} needs to be uploaded again</h2>
        {isServer ? (
          <p>
            The server restarted and no longer has this connection. Connect the database again from
            Data sources to keep asking questions.
          </p>
        ) : (
          <p>
            The server restarted and its copy of {source.name} was cleared, so there is nothing left
            to delete. Upload one of its files here to bring it back; its history and settings stay
            as they are. If it had several files, add the others afterwards from Data sources: they
            go into {source.name} next to this one and nothing is replaced.
            {isTally ? ' Use the Tally export (Excel or CSV) for a Tally report.' : ''}
          </p>
        )}
        {!isServer && !canUpload && (
          <p className="reupload-notice-hint">
            Ask the workspace owner or an admin to upload it again.
          </p>
        )}
        {error && (
          <p role="alert" className="reupload-notice-error">
            {error}
          </p>
        )}
      </div>
      {!isServer && canUpload && (
        <div className="reupload-notice-actions">
          <input
            ref={input}
            type="file"
            accept=".csv,.xlsx"
            className="reupload-notice-input"
            aria-label={`Choose a file to re-upload ${source.name}`}
            onChange={upload}
            disabled={uploading}
          />
          <button
            type="button"
            className="reupload-notice-button"
            onClick={() => input.current?.click()}
            disabled={uploading}
          >
            {uploading ? (
              <LoaderCircle size={16} className="voice-spinner" aria-hidden="true" />
            ) : (
              <FileUp size={16} aria-hidden="true" />
            )}
            {uploading ? 'Uploading…' : 'Re-upload file'}
          </button>
        </div>
      )}
    </section>
  );
}
