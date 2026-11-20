import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import AskData from '../pages/AskData';
import CalculatingStatus from '../components/CalculatingStatus';
import { clearChatSessions } from '../hooks/useChatState';
import { fetchApi } from '../api/client';
vi.mock('../context/AppContext', () => ({
  useAppContext: () => ({
    activeWorkspaceId: 'sales',
    activeWorkspace: { name: 'Sales' },
    refreshWorkspaces: vi.fn(),
  }),
}));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'owner', workspace_role: 'owner' } }),
}));
vi.mock('../api/client', () => ({ fetchApi: vi.fn(), fetchApiBlob: vi.fn() }));
afterEach(() => {
  clearChatSessions();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
function open() {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
  Element.prototype.scrollIntoView = vi.fn();
  return render(
    <MemoryRouter>
      <AskData />
    </MemoryRouter>,
  );
}
it.each(['enter', 'click'])(
  'replaces the arrow inline with calculating status during %s submission and removes it on completion',
  async (method) => {
    let finish;
    fetchApi.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { container } = open();
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'What is total revenue?' } });
    if (method === 'enter') fireEvent.keyDown(input, { key: 'Enter' });
    else fireEvent.click(screen.getByRole('button', { name: 'Run secure query' }));
    const status = screen.getByRole('status', { name: 'Calculating answer' });
    expect(status).toHaveTextContent('Calculating');
    expect(container.querySelector('.ask-prompt-actions').lastElementChild).toBe(status);
    expect(status.closest('.ask-prompt-controls')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Run secure query' })).not.toBeInTheDocument();
    expect(fetchApi).toHaveBeenCalledTimes(1);
    finish({ status: 'answer_unavailable', message: 'No revenue data is available.' });
    await waitFor(() =>
      expect(screen.queryByRole('status', { name: 'Calculating answer' })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: 'Run secure query' })).toBeEnabled();
  },
);
it('stops the animation on request failure and allows retry', async () => {
  let fail;
  fetchApi.mockImplementationOnce(
    () =>
      new Promise((resolve, reject) => {
        fail = reject;
      }),
  );
  open();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Show revenue' } });
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(screen.getByRole('status', { name: 'Calculating answer' })).toBeInTheDocument();
  fail(new Error('Connection interrupted'));
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: 'Calculating answer' })).not.toBeInTheDocument(),
  );
  expect(screen.getByRole('button', { name: 'Run secure query' })).toBeEnabled();
});
it('does not start processing for blank input or Shift+Enter', () => {
  open();
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Show revenue' } });
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', shiftKey: true });
  expect(screen.queryByRole('status', { name: 'Calculating answer' })).not.toBeInTheDocument();
  expect(fetchApi).not.toHaveBeenCalled();
});

it('uses a separate light orbit and keeps the burst paused until switching to dark', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  document.documentElement.dataset.theme = 'light';
  const { container, unmount } = render(<CalculatingStatus />);
  expect(container.querySelector('.calculating-light-orbit')).toHaveAttribute(
    'aria-hidden',
    'true',
  );
  expect(play).not.toHaveBeenCalled();
  document.documentElement.dataset.theme = 'dark';
  await waitFor(() => expect(play).toHaveBeenCalled());
  unmount();
  play.mockRestore();
  pause.mockRestore();
});
