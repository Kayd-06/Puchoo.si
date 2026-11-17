const count = (value) => Number.isSafeInteger(value) && value >= 0;
const format = (value) => (count(value) ? value.toLocaleString() : 'Unavailable');
const scope =
  'Planning, SQL generation, repairs and verification model calls. Excludes translation, speech and embeddings.';

export default function TokenUsage({ usage }) {
  const complete = usage?.status === 'complete' && count(usage.total_tokens);
  const partial = usage?.status === 'partial' && count(usage.reported_tokens);
  if (!complete && !partial) {
    return <p className="history-token-unavailable">Token usage unavailable</p>;
  }
  const calls = Array.isArray(usage.calls)
    ? usage.calls.filter((call) => call && typeof call === 'object')
    : [];
  return (
    <details className="history-token-usage" aria-label="Token usage">
      <summary>
        {complete
          ? `${format(usage.total_tokens)} tokens`
          : `${format(usage.reported_tokens)} reported tokens · partial usage`}
      </summary>
      <div className="history-token-breakdown">
        <span>Input: {format(usage.input_tokens)}</span>
        <span>Output: {format(usage.output_tokens)}</span>
        <span>
          {calls.length} model {calls.length === 1 ? 'call' : 'calls'}
        </span>
      </div>
      {partial && <p>Some model calls did not report usage. The complete total is unavailable.</p>}
      {calls.length > 0 && (
        <ul>
          {calls.map((call, index) => (
            <li key={index}>
              {call.stage || 'Model'} · {call.model || 'Unknown model'}:{' '}
              {count(call.total_tokens)
                ? `${format(call.total_tokens)} tokens`
                : 'Usage unavailable'}
            </li>
          ))}
        </ul>
      )}
      <p>{scope}</p>
    </details>
  );
}
