import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import SourceIllustration from './SourceIllustration';

describe('folder file count', () => {
  it('shows the batch size while uploading without claiming completion', () => {
    render(<SourceIllustration state="uploading" fileCount={3} />);
    expect(screen.getByText('3 files')).toBeInTheDocument();
    expect(screen.queryByText('Imported')).not.toBeInTheDocument();
  });

  it.each([1, 20])('ends the completion counter at the real %i-file batch size', (fileCount) => {
    const { container } = render(<SourceIllustration state="success" fileCount={fileCount} />);
    const counter = container.querySelector('.source-file-counter');
    expect(counter?.lastElementChild?.textContent).toBe(
      `${fileCount} ${fileCount === 1 ? 'file' : 'files'}`,
    );
    expect(screen.getByText('Imported')).toBeInTheDocument();
  });
});
