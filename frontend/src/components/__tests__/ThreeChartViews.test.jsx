import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ResultCharts from '../ResultCharts';
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal();
  const { cloneElement } = await import('react');
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => cloneElement(children, { width: 600, height: 300 }),
  };
});
const record = {
  id: 'three',
  columns: ['month', 'revenue', 'expenses'],
  rows: [
    { month: '2026-01-01', revenue: 10, expenses: 4 },
    { month: '2026-02-01', revenue: 20, expenses: 8 },
  ],
};
it('shows three independent chart dropdowns using the same returned data', () => {
  const save = vi.fn();
  render(<ResultCharts record={record} onConfigChange={save} />);
  const cards = [1, 2, 3].map((number) => screen.getByRole('region', { name: `Chart ${number}` }));
  expect(cards.map((card) => within(card).getByLabelText('Chart type').value)).toEqual([
    'line',
    'area',
    'bar',
  ]);
  fireEvent.change(within(cards[1]).getByLabelText('Chart type'), {
    target: { value: 'horizontal' },
  });
  fireEvent.change(within(cards[1]).getByLabelText('Metric'), { target: { value: 'expenses' } });
  expect(within(cards[0]).getByLabelText('Metric')).toHaveValue('revenue');
  expect(within(cards[1]).getByRole('img')).toHaveAccessibleName(/horizontal chart of expenses/);
  expect(within(cards[2]).getByLabelText('Chart type')).toHaveValue('bar');
  const saved = save.mock.lastCall[0];
  expect(saved.type).toBe('line');
  expect(saved.views[1]).toMatchObject({ type: 'horizontal', y: 'expenses' });
});
it('restores all views and accepts legacy primary-only preferences', () => {
  const config = { x: 'month', y: 'expenses', type: 'bar', aggregation: 'none' };
  const { rerender } = render(<ResultCharts record={record} config={config} />);
  expect(
    within(screen.getByRole('region', { name: 'Chart 1' })).getByLabelText('Metric'),
  ).toHaveValue('expenses');
  rerender(
    <ResultCharts
      record={record}
      config={{
        ...config,
        views: [config, { ...config, type: 'area' }, { ...config, type: 'step' }],
      }}
    />,
  );
  expect(
    within(screen.getByRole('region', { name: 'Chart 3' })).getByLabelText('Chart type'),
  ).toHaveValue('step');
});
it('renders no graphs when the result cannot be charted', () => {
  render(<ResultCharts record={{ columns: ['name'], rows: [{ name: 'A' }, { name: 'B' }] }} />);
  expect(screen.queryByRole('region', { name: 'Three chart views' })).not.toBeInTheDocument();
});

it('keeps secondary chart controls tucked away until settings are opened', () => {
  render(<ResultCharts record={record} />);
  const first = within(screen.getByRole('region', { name: 'Chart 1' }));
  expect(first.getByLabelText('Chart type')).toBeVisible();
  expect(first.getByLabelText('Metric')).toBeVisible();
  expect(first.queryByRole('group', { name: 'Chart color' })).not.toBeInTheDocument();
  fireEvent.click(first.getByRole('button', { name: 'Advanced settings' }));
  expect(first.getByRole('group', { name: 'Chart color' })).toBeVisible();
  fireEvent.click(first.getByRole('button', { name: 'Advanced settings' }));
  expect(first.queryByRole('group', { name: 'Chart color' })).not.toBeInTheDocument();
  expect(first.getByRole('img')).toHaveAccessibleName(/line chart/);
});
