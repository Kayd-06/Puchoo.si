import { useId, useState } from 'react';
import { ChevronDown, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';

export default function OrganizationDeletePanel() {
  const { user, deleteOrganization } = useAuth();
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [password, setPassword] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const organizationName = user?.workspace_name || user?.institute_name || '';
  const organizationKind = ['institution', 'institute'].includes(user?.workspace_type)
    ? 'school'
    : user?.workspace_type === 'business'
      ? 'business'
      : null;

  async function confirmDeletion(event) {
    event.preventDefault();
    if (deleting || !organizationName || confirmation !== organizationName || !password) return;
    setDeleting(true);
    setDeleteError('');
    try {
      await deleteOrganization(confirmation, password);
      navigate('/login', { replace: true });
    } catch (error) {
      setPassword('');
      setDeleteError(error.message || 'Could not delete the organization. Please try again.');
      setDeleting(false);
    }
  }

  if (user?.role !== 'owner' || !organizationKind) return null;
  return (
    <section className="settings-organization-disclosure" aria-label="Organization controls">
      <button
        type="button"
        className="settings-organization-summary"
        aria-label="Organization controls"
        aria-expanded={expanded}
        aria-controls={detailsId}
        disabled={deleting}
        onClick={() => {
          setExpanded((value) => !value);
          setDeleteOpen(false);
          setConfirmation('');
          setPassword('');
          setDeleteError('');
        }}
      >
        <span>
          <Trash2 size={15} aria-hidden="true" />
          Organization controls
        </span>
        <span className="settings-organization-scope">Account-wide</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {expanded && (
        <div id={detailsId}>
          <section
            className="settings-section settings-danger-zone settings-organization-delete"
            aria-labelledby="delete-organization-title"
          >
            <div className="settings-card-label">
              <Trash2 size={16} />
              <span>Organization owner controls</span>
              <small>{organizationKind === 'school' ? 'School' : 'Business'}</small>
            </div>
            <h2 id="delete-organization-title">
              Delete {organizationKind}: {organizationName}
            </h2>
            <p>Permanently close {organizationName} and remove its data and accounts.</p>
            {!deleteOpen ? (
              <button
                type="button"
                className="organization-delete-button"
                onClick={() => setDeleteOpen(true)}
              >
                Delete {organizationKind}
              </button>
            ) : (
              <form onSubmit={confirmDeletion} className="organization-delete-form">
                <p role="note">
                  This permanently deletes this {organizationKind}, your owner account, all member
                  accounts, uploaded data, saved connections, and query history. Everyone will be
                  signed out. External databases are not deleted. This cannot be undone.
                </p>
                <label htmlFor="organization-confirmation">
                  Type {organizationName} to confirm
                </label>
                <input
                  id="organization-confirmation"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  disabled={deleting}
                  autoComplete="off"
                  autoFocus
                />
                <label htmlFor="organization-password">Your current password</label>
                <input
                  id="organization-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={deleting}
                  autoComplete="current-password"
                  maxLength={1024}
                />
                {deleteError && <p role="alert">{deleteError}</p>}
                <div className="organization-delete-actions">
                  <button
                    type="button"
                    className="settings-action"
                    disabled={deleting}
                    onClick={() => {
                      setDeleteOpen(false);
                      setConfirmation('');
                      setPassword('');
                      setDeleteError('');
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="organization-delete-button"
                    disabled={
                      deleting ||
                      !organizationName ||
                      confirmation !== organizationName ||
                      !password
                    }
                  >
                    {deleting ? 'Deleting…' : `Permanently delete ${organizationKind}`}
                  </button>
                </div>
              </form>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
