import { readFileSync } from 'node:fs';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import TokenUsage from '../components/TokenUsage';

let sheet;
afterEach(() => {
  sheet?.remove();
  document.documentElement.removeAttribute('data-theme');
});

// jsdom computes CSS cascade but leaves var() references unresolved. Resolve
// those against the element's actual inherited custom properties for contrast.
function resolveColor(element, expression) {
  return expression.replace(/var\((--[\w-]+)(?:,\s*([^()]*))?\)/g, (_, name, fallback) => {
    for (let node = element; node; node = node.parentElement) {
      const value = getComputedStyle(node).getPropertyValue(name).trim();
      if (value) return resolveColor(node, value);
    }
    return fallback || '';
  });
}
function luminance(color) {
  const normalized = color.trim();
  const channels = normalized.startsWith('#')
    ? (normalized.length === 4
        ? [...normalized.slice(1)].map((c) => c + c)
        : normalized.slice(1).match(/../g)
      ).map((c) => parseInt(c, 16))
    : normalized
        .match(/[\d.]+/g)
        .slice(0, 3)
        .map(Number);
  const [r, g, b] = channels
    .map((c) => c / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return r * 0.2126 + g * 0.7152 + b * 0.0722;
}
function contrast(node, foreground, background) {
  const a = luminance(resolveColor(node, foreground));
  const b = luminance(resolveColor(node, background));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

it.each(['dark', 'light'])(
  'keeps token details and inherited workspace headings readable in %s mode',
  (theme) => {
    document.documentElement.dataset.theme = theme;
    sheet = document.createElement('style');
    sheet.textContent = [
      'src/index.css',
      'src/styles/history.css',
      'src/styles/lightPalette.css',
      'src/styles/darkPalette.css',
    ]
      .map((path) => readFileSync(path, 'utf8'))
      .join('\n');
    document.head.append(sheet);
    render(
      <div id="root">
        <div className="app-container">
          <main className="history-page-clean">
            <h2 className="inherited-heading">Usage overview</h2>
            <TokenUsage
              usage={{
                status: 'complete',
                input_tokens: 1200,
                output_tokens: 444,
                total_tokens: 1644,
                calls: [],
              }}
            />
          </main>
        </div>
      </div>,
    );
    const summary = screen.getByText('1,644 tokens');
    const background = 'var(--bg-main)';
    expect(
      contrast(summary, getComputedStyle(summary).color, background),
      'token summary contrast',
    ).toBeGreaterThanOrEqual(4.5);
    const heading = screen.getByText('Usage overview');
    expect(
      contrast(heading, getComputedStyle(heading).color, background),
      'inherited text contrast',
    ).toBeGreaterThanOrEqual(4.5);
    fireEvent.click(summary);
    const breakdown = screen.getByText('Input: 1,200');
    expect(
      contrast(breakdown, getComputedStyle(breakdown).color, background),
      'expanded token contrast',
    ).toBeGreaterThanOrEqual(4.5);
  },
);
