import { Check, X } from 'lucide-react';
import '../../styles/tallyImport.css';

export default function HeaderRowPicker({
  fileName,
  rows,
  selectedRow,
  onSelect,
  onConfirm,
  onCancel,
  busy = false,
}) {
  return (
    <section className="tally-preview header-row-picker" aria-labelledby="header-picker-title">
      <header className="tally-preview-header">
        <span className="tally-preview-badge">Layout not recognised</span>
        <h2 id="header-picker-title">Which row has the column names?</h2>
        <p className="tally-preview-file">
          {fileName}. Rows above the one you pick (titles, addresses) are left out.
        </p>
      </header>
      <div className="tally-preview-table-wrap" role="radiogroup" aria-label="Header row">
        <table className="tally-preview-table header-row-table">
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={index}
                className={
                  index === selectedRow ? 'is-selected' : index < selectedRow ? 'is-skipped' : ''
                }
              >
                <th scope="row">
                  <label>
                    <input
                      type="radio"
                      name="header-row"
                      checked={index === selectedRow}
                      onChange={() => onSelect(index)}
                      aria-label={`Use row ${index + 1} as the header`}
                    />
                    {index + 1}
                  </label>
                </th>
                {row.map((cell, column) => (
                  <td key={column}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tally-preview-actions">
        <button type="button" className="tally-preview-cancel" onClick={onCancel} disabled={busy}>
          <X size={15} /> Cancel
        </button>
        <button type="button" className="connect-submit" onClick={onConfirm} disabled={busy}>
          <Check size={15} /> {busy ? 'Saving…' : `Use row ${selectedRow + 1} as header`}
        </button>
      </div>
    </section>
  );
}
