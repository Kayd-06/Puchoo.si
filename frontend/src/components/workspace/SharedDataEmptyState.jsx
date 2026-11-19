import { useState } from 'react';
import { Database, LoaderCircle } from 'lucide-react';

export default function SharedDataEmptyState({ refreshWorkspaces, loading = false }) {
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [checked, setChecked] = useState(false);

  async function checkForData() {
    if (checking) return;
    setChecking(true);
    setError('');
    try {
      await refreshWorkspaces();
      setChecked(true);
    } catch {
      setError('Could not check for shared data. Please try again.');
    } finally {
      setChecking(false);
    }
  }

  return (
    <section className="ask-viewer-state ask-shared-empty" aria-labelledby="shared-data-heading">
      <div className="ask-viewer-icon" aria-hidden="true">
        <Database size={22} />
      </div>
      <span>Shared workspace</span>
      <h1 id="shared-data-heading">
        {loading ? 'Loading shared data…' : 'Waiting for shared data.'}
      </h1>
      <p>
        Your workspace owner or admin can add data for your team. Once it is shared, it will appear
        here automatically.
      </p>
      <button
        type="button"
        className="ask-primary-action"
        onClick={checkForData}
        disabled={loading || checking}
      >
        {checking && <LoaderCircle size={16} aria-hidden="true" />}
        {checking ? 'Checking…' : 'Check for shared data'}
      </button>
      {error ? (
        <p className="ask-shared-feedback" role="alert">
          {error}
        </p>
      ) : checked ? (
        <p className="ask-shared-feedback" role="status">
          No shared data yet. We’ll keep checking for updates.
        </p>
      ) : null}
    </section>
  );
}
