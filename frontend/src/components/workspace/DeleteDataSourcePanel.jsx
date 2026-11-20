import { useState } from 'react';
import { Database } from 'lucide-react';
import { fetchApi } from '../../api/client';
import { useAppContext } from '../../context/AppContext';
import { useAuth } from '../../context/AuthContext';
import { chatScope, invalidateHistory } from '../../hooks/useChatState';

export default function DeleteDataSourcePanel() {
  const { activeWorkspace, removeWorkspace } = useAppContext();
  const { user } = useAuth();
  const [deletedName, setDeletedName] = useState('');
  // After a delete, show only the outcome. The next source's panel stays hidden
  // until the owner dismisses it, so a second delete is always a deliberate step.
  if (deletedName) {
    return (
      <section
        className="settings-section settings-danger-zone settings-delete-source settings-source-delete-done"
        aria-label="Data source deleted"
      >
        <p role="status" className="settings-source-delete-feedback">
          {deletedName} was deleted. Your organization and accounts are unchanged.
        </p>
        <button type="button" className="settings-action" onClick={() => setDeletedName('')}>
          Done
        </button>
      </section>
    );
  }
  if (!activeWorkspace) return null;
  return (
    <SourceConfirmation
      key={activeWorkspace.id}
      source={activeWorkspace}
      onDeleted={() => {
        invalidateHistory(chatScope(user, activeWorkspace.id));
        setDeletedName(activeWorkspace.name);
        removeWorkspace(activeWorkspace.id);
      }}
    />
  );
}

function SourceConfirmation({ source, onDeleted }) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const isServer = source.source_type === 'server';
  async function deleteSource(event) {
    event.preventDefault();
    if (deleting || confirmation !== source.name) return;
    setDeleting(true);
    setError('');
    try {
      await fetchApi(`/workspaces/${encodeURIComponent(source.id)}`, { method: 'DELETE' });
      onDeleted();
    } catch (failure) {
      setError(failure.message || 'Could not delete this data source. Try again.');
      setDeleting(false);
    }
  }
  return (
    <section
      className="settings-section settings-danger-zone settings-delete-source"
      aria-labelledby="delete-source-title"
    >
      <div className="settings-delete-source-icon" aria-hidden="true">
        <Database size={18} />
      </div>
      <div className="settings-delete-source-copy">
        <span className="settings-section-kicker">
          Selected data source · {isServer ? 'Database connection' : 'Uploaded data'}
        </span>
        <h2 id="delete-source-title">
          {isServer ? 'Disconnect' : 'Delete'} {source.name}
        </h2>
        <p>
          Removes only this source and its query history.
          {isServer ? ' The external database stays unchanged.' : ''}
        </p>
      </div>
      {!open ? (
        <button type="button" className="organization-delete-button" onClick={() => setOpen(true)}>
          Delete data source
        </button>
      ) : (
        <form className="organization-delete-form" onSubmit={deleteSource}>
          <p>
            Your organization and member accounts stay active. This action affects only{' '}
            {source.name}.
          </p>
          <label htmlFor="source-confirmation">Type {source.name} to confirm</label>
          <input
            id="source-confirmation"
            autoFocus
            autoComplete="off"
            value={confirmation}
            disabled={deleting}
            onChange={(event) => setConfirmation(event.target.value)}
          />
          {error && <p role="alert">{error}</p>}
          <div className="organization-delete-actions">
            <button
              type="button"
              className="settings-action"
              disabled={deleting}
              onClick={() => {
                setOpen(false);
                setConfirmation('');
                setError('');
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="organization-delete-button"
              disabled={deleting || confirmation !== source.name}
            >
              {deleting ? 'Deleting…' : 'Permanently delete data source'}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
