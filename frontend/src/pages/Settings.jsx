import '../styles/sessionHistory.css';
import '../styles/settingsTheme.css';
import { History as HistoryIcon, ShieldCheck } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAppContext } from '../context/AppContext';
import { useAuth } from '../context/AuthContext';
import AccountProfile from '../components/AccountProfile';
import OrganizationDeletePanel from '../components/workspace/OrganizationDeletePanel';
import DeleteDataSourcePanel from '../components/workspace/DeleteDataSourcePanel';
import CompanyFilesPanel from '../components/workspace/CompanyFilesPanel';
import WorkspaceInvitePanel from '../components/workspace/WorkspaceInvitePanel';
import { can } from '../auth/capabilities';

export default function Settings() {
  const { user } = useAuth();
  const { activeWorkspace } = useAppContext();
  const navigate = useNavigate();

  return (
    <div className="settings-page settings-page-refined">
      <header className="settings-hero">
        <div className="settings-eyebrow">
          <ShieldCheck size={20} />
          <span>System preferences</span>
        </div>
        <div className="settings-hero-copy">
          <div>
            <h1>Settings</h1>
            <p>Control your profile, workspace access, and verified account settings.</p>
          </div>
        </div>
      </header>

      <div className="settings-flow">
        <div className="settings-account-column">
          <AccountProfile />

          <section
            className="settings-section settings-history session-history-refined"
            aria-labelledby="session-history-title"
          >
            <div className="settings-history-icon">
              <HistoryIcon size={19} />
            </div>
            <div>
              <span className="settings-section-kicker">Workspace records</span>
              <h2 id="session-history-title">Session &amp; history</h2>
              <p>Review queries and results for the selected source.</p>
            </div>
            <button className="settings-action" onClick={() => navigate('/history')}>
              Manage history <span aria-hidden="true">↗</span>
            </button>
          </section>
          {can(user, 'query_workspace') && (
            <CompanyFilesPanel key={`company-files-${activeWorkspace?.id || 'none'}`} />
          )}
          {user?.role === 'owner' && <DeleteDataSourcePanel />}
          {user?.role === 'owner' && (
            <OrganizationDeletePanel key={activeWorkspace?.id || 'no-source'} />
          )}
        </div>
        {can(user, 'administer_workspace') && (
          <div className="settings-team-column">
            <WorkspaceInvitePanel />
          </div>
        )}
      </div>
    </div>
  );
}
