import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkspaceInvitePanel from '../WorkspaceInvitePanel';
import { listWorkspaceMembers, updateWorkspaceMemberRole } from '../../../api/auth';

let user;

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user }),
}));

vi.mock('../../../api/auth', () => ({
  createWorkspaceInvite: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  updateWorkspaceMemberRole: vi.fn(),
  removeWorkspaceMember: vi.fn(),
}));

describe('WorkspaceInvitePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    user = {
      id: 'admin-id',
      role: 'admin',
      workspace_role: 'admin',
      workspace_type: 'business',
      workspace_name: 'Acme',
      capabilities: { administer_workspace: true },
    };
    listWorkspaceMembers.mockResolvedValue([
      { id: 'owner-id', full_name: 'Olivia Owner', email: 'owner@acme.test', role: 'owner' },
      { id: 'editor-id', full_name: 'Eddie Editor', email: 'editor@acme.test', role: 'editor' },
    ]);
  });

  it('lets an admin invite people but keeps member roles read-only', async () => {
    render(<WorkspaceInvitePanel />);

    expect(screen.getByRole('heading', { name: /invite people to acme/i })).toBeInTheDocument();
    expect(await screen.findByText('Eddie Editor')).toBeInTheDocument();
    expect(screen.queryByLabelText(/role for eddie editor/i)).not.toBeInTheDocument();
    expect(updateWorkspaceMemberRole).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /remove eddie editor/i })).toHaveClass(
      'workspace-member-remove',
    );
    expect(screen.queryByRole('button', { name: /remove olivia owner/i })).not.toBeInTheDocument();
  });

  it('lets the owner open the role menu and save a selected role', async () => {
    user = { ...user, id: 'owner-id', role: 'owner', workspace_role: 'owner' };
    updateWorkspaceMemberRole.mockResolvedValue({
      id: 'editor-id',
      full_name: 'Eddie Editor',
      email: 'editor@acme.test',
      role: 'viewer',
    });
    render(<WorkspaceInvitePanel />);
    const trigger = await screen.findByRole('button', { name: 'Role for Eddie Editor' });
    fireEvent.click(trigger);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'editor' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('option', { name: 'viewer' }));
    await waitFor(() =>
      expect(updateWorkspaceMemberRole).toHaveBeenCalledWith('editor-id', 'viewer'),
    );
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('hides admin invites and admin removal from admins but not from the owner', async () => {
    listWorkspaceMembers.mockResolvedValue([
      { id: 'owner-id', full_name: 'Olivia Owner', email: 'owner@acme.test', role: 'owner' },
      { id: 'admin-2', full_name: 'Ada Admin', email: 'ada@acme.test', role: 'admin' },
    ]);
    const view = render(<WorkspaceInvitePanel />);
    expect(await screen.findByText('Ada Admin')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'admin' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'editor' })).toBeInTheDocument();
    expect(screen.getByText('Admin')).toHaveClass('workspace-member-owner');
    expect(screen.queryByRole('button', { name: /remove ada admin/i })).not.toBeInTheDocument();
    view.unmount();

    user = { ...user, id: 'owner-id', role: 'owner', workspace_role: 'owner' };
    render(<WorkspaceInvitePanel />);
    expect(await screen.findByText('Ada Admin')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'admin' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /remove ada admin/i })).toBeInTheDocument();
  });

  it('stays hidden when the capability is absent', async () => {
    user = { ...user, role: 'editor', capabilities: { administer_workspace: false } };
    const { container } = render(<WorkspaceInvitePanel />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(listWorkspaceMembers).not.toHaveBeenCalled();
  });
});
