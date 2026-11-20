import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { can } from '../../auth/capabilities';
import Sidebar from '../../components/layout/Sidebar';
import TopBar from '../../components/layout/TopBar';
import ConnectData from '../ConnectData';
import Settings from '../Settings';

let user;
let workspaceState;

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user, updateProfileImage: vi.fn() }),
}));
vi.mock('../../context/AppContext', () => ({
  useAppContext: () => workspaceState,
}));
vi.mock('../../components/workspace/WorkspaceInvitePanel', () => ({
  default: () => <div>Invite management</div>,
}));
vi.mock('../../components/auth/ChangeEmailPanel', () => ({
  default: () => <div>Change email</div>,
}));
vi.mock('../../api/client', () => ({
  fetchApi: vi.fn().mockResolvedValue({ items: [], unread_count: 0 }),
}));
vi.mock('../../context/ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

const capabilityMap = {
  owner: {
    view_workspaces: true,
    view_history: true,
    query_workspace: true,
    manage_data_sources: true,
    administer_workspace: true,
    manage_account: true,
  },
  admin: {
    view_workspaces: true,
    view_history: true,
    query_workspace: true,
    manage_data_sources: true,
    administer_workspace: true,
    manage_account: true,
  },
  editor: {
    view_workspaces: true,
    view_history: true,
    query_workspace: true,
    manage_data_sources: false,
    administer_workspace: false,
    manage_account: true,
  },
  viewer: {
    view_workspaces: true,
    view_history: true,
    query_workspace: false,
    manage_data_sources: false,
    administer_workspace: false,
    manage_account: true,
  },
};

function setRole(role) {
  user = {
    id: `${role}-id`,
    full_name: 'Ada Lovelace',
    email: 'ada@example.com',
    role,
    workspace_role: role,
    workspace_type: 'business',
    workspace_name: 'Acme',
    capabilities: capabilityMap[role],
  };
}

function renderRoute(component) {
  return render(<MemoryRouter>{component}</MemoryRouter>);
}

describe('role-aware product surfaces', () => {
  afterEach(() => vi.useRealTimers());

  beforeEach(() => {
    setRole('owner');
    workspaceState = {
      workspaces: [],
      activeWorkspace: null,
      activeWorkspaceId: null,
      setActiveWorkspaceId: vi.fn(),
      setWorkspaces: vi.fn(),
      addWorkspace: vi.fn(),
    };
  });

  it.each(['owner', 'admin'])('%s sees data-source and admin surfaces', (role) => {
    setRole(role);
    renderRoute(<Sidebar />);
    expect(screen.getByText('Data sources')).toBeInTheDocument();
    renderRoute(<Settings />);
    expect(screen.getByText('Invite management')).toBeInTheDocument();
    expect(screen.getByText(`Business · ${role}`)).toBeInTheDocument();
  });

  it('editor can ask and review but cannot see or open uploads', () => {
    setRole('editor');
    renderRoute(<Sidebar />);
    expect(screen.getByText('Ask data')).toBeInTheDocument();
    expect(screen.getByText('History')).toBeInTheDocument();
    expect(screen.queryByText('Data sources')).not.toBeInTheDocument();
    renderRoute(<ConnectData />);
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument();
    expect(document.getElementById('fileUpload')).toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.getByText(/does not allow data source changes/i)).toBeInTheDocument();
  });

  it('limits data-source selection to CSV and Excel files', () => {
    renderRoute(<ConnectData />);
    expect(document.getElementById('fileUpload')).toHaveAttribute('accept', '.csv,.xlsx');
    expect(screen.getByText(/upload CSV or Excel files/i)).toBeInTheDocument();
  });

  it('updates the decorative source word for upload, server and selected files', () => {
    renderRoute(<ConnectData />);
    expect(screen.getByText('DATA')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /connect server/i }));
    expect(screen.getByText('LINK')).toBeInTheDocument();
    expect(screen.queryByText('DATA')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /upload files/i }));
    const file = new File(['id,total\n1,20'], 'sales.csv', { type: 'text/csv' });
    fireEvent.change(document.getElementById('fileUpload'), { target: { files: [file] } });
    expect(screen.getByText('READY')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove sales.csv' }));
    expect(screen.getByText('DATA')).toBeInTheDocument();
  });

  it('viewer sees history and account settings only', () => {
    setRole('viewer');
    renderRoute(<Sidebar />);
    expect(screen.getByText('History')).toBeInTheDocument();
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.queryByText('Ask data')).not.toBeInTheDocument();
    expect(screen.queryByText('Data sources')).not.toBeInTheDocument();
    renderRoute(<Settings />);
    expect(screen.getByText('Upload image')).toBeInTheDocument();
    expect(screen.queryByText('Invite management')).not.toBeInTheDocument();
    expect(screen.getByText('Business · viewer')).toBeInTheDocument();
  });

  it('keeps database connection fields available after switching tabs', () => {
    renderRoute(<ConnectData />);
    fireEvent.click(screen.getByRole('tab', { name: /connect server/i }));
    expect(screen.getByRole('tab', { name: /connect server/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    for (const name of [
      'Workspace name',
      'Server host',
      'Database name',
      'Read-only username',
      'Password',
    ]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText('Database engine'), { target: { value: 'mysql' } });
    expect(screen.getByLabelText('Database port')).toHaveValue(3306);
    expect(screen.getByRole('button', { name: /connect source/i })).toBeEnabled();
  });

  it('capabilities deny missing and unknown permissions', () => {
    expect(can(null, 'view_history')).toBe(false);
    expect(can({ capabilities: {} }, 'view_history')).toBe(false);
    expect(can(user, 'does_not_exist')).toBe(false);
  });

  it.each(['owner', 'admin', 'editor', 'viewer'])('%s can manage a profile image', (role) => {
    setRole(role);
    renderRoute(<Settings />);
    expect(screen.getByText('Upload image')).toBeInTheDocument();
    // External image links were removed for privacy; only uploads are offered.
    expect(screen.queryByText('Use image link')).not.toBeInTheDocument();
  });

  it('offers upload only in the profile image editor and states the 200 KB cap', () => {
    setRole('owner');
    renderRoute(<Settings />);
    fireEvent.click(screen.getByText('Upload image'));
    expect(screen.queryByRole('tab', { name: /image link/i })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/https:\/\//)).not.toBeInTheDocument();
    expect(screen.getAllByText(/200 KB/).length).toBeGreaterThan(0);
  });

  it('flips one card and exposes only its active face', () => {
    setRole('owner');
    const { container } = renderRoute(<Settings />);
    expect(container.querySelectorAll('.profile-identity-sheet')).toHaveLength(1);
    expect(container.querySelectorAll('.profile-layer')).toHaveLength(0);
    const front = container.querySelector('.profile-card-front');
    const back = container.querySelector('.profile-card-back');
    expect(front).toHaveAttribute('aria-hidden', 'false');
    expect(back).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'View profile details' }));
    expect(front).toHaveAttribute('aria-hidden', 'true');
    expect(back).toHaveAttribute('aria-hidden', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'Hide profile details' }));
    expect(front).toHaveAttribute('aria-hidden', 'false');
    expect(back).toHaveAttribute('aria-hidden', 'true');
  });

  it('reveals real profile details without introducing payment controls', () => {
    setRole('owner');
    renderRoute(<Settings />);
    const reveal = screen.getByRole('button', { name: 'View profile details' });
    expect(reveal).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(reveal);
    expect(screen.getByRole('button', { name: 'Hide profile details' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    fireEvent.click(screen.getByRole('switch', { name: 'Show email' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Show workspace details' }));
    expect(screen.getByRole('heading', { name: 'Ada Lovelace' })).toBeVisible();
    expect(screen.getByRole('switch', { name: 'Show email' })).toHaveTextContent('ada@example.com');
    expect(screen.getByRole('switch', { name: 'Show workspace details' })).toHaveTextContent(
      'Acme',
    );
    expect(screen.getByText('Change email')).toBeInTheDocument();
    expect(screen.queryByText(/CVV|View PIN|Lock card|card number/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide profile details' }));
    expect(screen.queryByText('ada@example.com')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload image' })).toBeVisible();
  });

  it('independently reveals email and workspace data and masks them again when closed', () => {
    setRole('owner');
    renderRoute(<Settings />);
    fireEvent.click(screen.getByRole('button', { name: 'View profile details' }));
    const email = screen.getByRole('switch', { name: 'Show email' });
    const workspace = screen.getByRole('switch', { name: 'Show workspace details' });
    expect(email).toHaveAttribute('aria-checked', 'false');
    expect(workspace).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByText('ada@example.com')).not.toBeInTheDocument();
    expect(screen.queryByText('Acme')).not.toBeInTheDocument();
    fireEvent.click(email);
    expect(email).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Show email' })).toHaveTextContent('ada@example.com');
    expect(screen.queryByText('Acme')).not.toBeInTheDocument();
    fireEvent.click(workspace);
    expect(screen.getByRole('switch', { name: 'Show workspace details' })).toHaveTextContent(
      'Acme',
    );
    fireEvent.click(email);
    expect(screen.queryByText('ada@example.com')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide profile details' }));
    fireEvent.click(screen.getByRole('button', { name: 'View profile details' }));
    expect(screen.getByRole('switch', { name: 'Show workspace details' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('uses the account image in the top bar', () => {
    setRole('editor');
    user.image_url = 'data:image/png;base64,iVBORw0KGgo=';
    const { container } = renderRoute(<TopBar />);
    expect(container.querySelector('.topbar-avatar img')).toHaveAttribute('src', user.image_url);
    expect(container.querySelector('.topbar-avatar img')).toHaveAttribute(
      'referrerpolicy',
      'no-referrer',
    );
  });

  it('keeps a long account email on one truncatable line', () => {
    setRole('owner');
    user.email = 'academicplanningsystem@gmail.com';
    renderRoute(<TopBar />);
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    const email = screen.getByText(user.email);
    expect(email).toHaveClass('topbar-account-email');
    expect(email).toHaveAttribute('title', user.email);
    expect(email.parentElement).toHaveClass('topbar-account-copy');
  });

  it('selects an active data source from the keyboard-accessible sidebar menu', () => {
    const setActiveWorkspaceId = vi.fn();
    workspaceState = {
      ...workspaceState,
      workspaces: [
        { id: 'source-a', name: 'Revenue data' },
        { id: 'source-b', name: 'Expense data' },
      ],
      activeWorkspace: { id: 'source-a', name: 'Revenue data' },
      activeWorkspaceId: 'source-a',
      setActiveWorkspaceId,
    };
    renderRoute(<Sidebar />);
    fireEvent.click(screen.getByRole('button', { name: /select active data source/i }));
    expect(screen.getByRole('listbox', { name: 'Active data sources' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: 'Expense data' }));
    expect(setActiveWorkspaceId).toHaveBeenCalledWith('source-b');
    expect(screen.getByRole('listbox', { name: 'Active data sources' })).toHaveClass('is-closing');
  });

  it('keeps the active-source menu mounted while its close animation plays', () => {
    vi.useFakeTimers();
    workspaceState = {
      ...workspaceState,
      workspaces: [{ id: 'source-a', name: 'Revenue data' }],
      activeWorkspace: { id: 'source-a', name: 'Revenue data' },
      activeWorkspaceId: 'source-a',
    };
    renderRoute(<Sidebar />);
    const trigger = screen.getByRole('button', { name: /select active data source/i });

    fireEvent.click(trigger);
    fireEvent.click(trigger);

    expect(screen.getByRole('listbox', { name: 'Active data sources' })).toHaveClass('is-closing');
    act(() => vi.advanceTimersByTime(180));
    expect(screen.queryByRole('listbox', { name: 'Active data sources' })).not.toBeInTheDocument();
  });
});
