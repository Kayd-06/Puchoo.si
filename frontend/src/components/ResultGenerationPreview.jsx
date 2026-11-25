import '../styles/resultGeneration.css';

// Empty cells are placeholders, never estimated or invented result data.
export default function ResultGenerationPreview() {
  return (
    <div className="ask-query-transition" aria-hidden="true">
      <div className="ask-generation-sheet">
        <div className="ask-generation-header">
          <span />
          <i />
          <i />
          <i />
        </div>
        {Array.from({ length: 8 }, (_, row) => (
          <div className="ask-generation-row" key={row} style={{ '--sheet-row': row }}>
            <span>{row + 1}</span>
            {Array.from({ length: 3 }, (_, column) => (
              <div key={column}>
                <i style={{ width: `${[64, 80, 48, 72, 58][(row + column) % 5]}%` }} />
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="ask-generation-caption">
        <i />
        Generating your answer
      </div>
    </div>
  );
}
