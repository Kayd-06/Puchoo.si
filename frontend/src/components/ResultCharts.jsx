import { useState } from 'react';
import { defaultChart, resolveChart } from '../utils/resultCharts';
import ResultChartCard from './ResultChartCard';
import './resultCharts.css';

export default function ResultCharts({ record, presentation = {}, config, onConfigChange }) {
  const [localConfig, setLocalConfig] = useState(null);
  const initial = defaultChart(record);
  if (!initial) return null;
  const options = resolveChart(record, initial).options;
  const secondary = initial.type === 'line' ? 'area' : 'horizontal';
  const third = initial.type === 'line' ? 'bar' : options.includes('donut') ? 'donut' : 'bar';
  const defaults = [
    { ...initial, colorIndex: 0 },
    { ...initial, type: secondary, colorIndex: 1 },
    { ...initial, type: third, colorIndex: 2 },
  ];
  const saved = config || localConfig;
  const views = defaults.map((fallback, index) => {
    const candidate = Array.isArray(saved?.views) ? saved.views[index] : index === 0 ? saved : null;
    return candidate && typeof candidate === 'object' && !Array.isArray(candidate)
      ? candidate
      : fallback;
  });
  const update = (index, next) => {
    const nextViews = views.map((view, i) => (i === index ? next : view));
    // Keep the primary view at the top level for existing report/PDF exports.
    const updated = { ...nextViews[0], views: nextViews };
    setLocalConfig(updated);
    onConfigChange?.(updated);
  };
  return (
    <section className="result-chart-dashboard" aria-label="Three chart views">
      <header className="result-chart-dashboard-heading">
        <div>
          <h2>Visualizations</h2>
          <p>
            {initial.aggregation === 'count'
              ? 'Category counts from the returned rows'
              : 'Three views of your results'}
          </p>
        </div>
        <span className="result-chart-row-count">{record.rows.length.toLocaleString()} rows</span>
      </header>
      {record.truncated && (
        <p className="result-chart-source-note" role="status">
          Partial results: charts use only the returned {record.row_count ?? record.rows.length}{' '}
          rows.
        </p>
      )}
      <div className="result-chart-grid">
        {views.map((view, index) => (
          <ResultChartCard
            key={index}
            record={record}
            presentation={presentation}
            config={view}
            recommendation={defaults[index]}
            title={`Chart ${index + 1}`}
            showSourceNotice={false}
            onConfigChange={(next) => update(index, next)}
          />
        ))}
      </div>
    </section>
  );
}
