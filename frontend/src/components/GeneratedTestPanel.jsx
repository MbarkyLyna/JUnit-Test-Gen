import { useState } from 'react';

const STATUS_LABELS = {
  full_coverage: 'Full Coverage (≥80%)',
  partial_coverage: 'Partial Coverage',
  failed_to_compile: 'Failed to Compile',
  no_change: 'No Coverage Delta',
};

function ResultCard({ result, index }) {
  const [expanded, setExpanded] = useState(index === 0);
  const hasSource = Boolean(result.test_source);

  return (
    <li className={`result-card ${result.status}`}>
      <div className="result-top">
        <button
          type="button"
          className="result-expand-btn"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          <span className={`result-chevron ${expanded ? 'expanded' : ''}`} />
          <strong className="class-name">
            {result.class_fqcn || result.class_name || result.class_path}
          </strong>
        </button>
        <span className={`status-pill ${result.status}`}>
          {STATUS_LABELS[result.status] || result.status}
        </span>
      </div>
      <div className="result-paths">
        <span className="src-path">{result.class_path}</span>
        {result.test_path && <span className="arrow"> → {result.test_path}</span>}
      </div>
      <div className="result-metrics">
        <span className={result.tests_passed ? 'metric-pass' : 'metric-fail'}>
          {result.tests_passed ? '✓ Compiled & passed' : '✗ Failed verification'}
        </span>
        <span>Initial: {result.initial_coverage_pct}%</span>
        <span>Final: {result.final_coverage_pct}%</span>
        <span className={result.coverage_delta > 0 ? 'delta-pos' : 'delta-zero'}>
          Delta: {result.coverage_delta > 0 ? `+${result.coverage_delta}%` : '0%'}
        </span>
      </div>
      {result.message && <div className="result-msg">{result.message}</div>}
      {expanded && hasSource && (
        <div className="test-source-panel">
          <div className="test-source-header">
            <span>Generated test source</span>
            {result.test_path && <code>{result.test_path}</code>}
          </div>
          <pre className="test-source-code"><code>{result.test_source}</code></pre>
        </div>
      )}
      {expanded && !hasSource && (
        <div className="test-source-empty">No test source available for this class.</div>
      )}
    </li>
  );
}

export default function GeneratedTestPanel({ results }) {
  if (!results?.length) return null;

  return (
    <div className="results panel">
      <div className="panel-header">Generated Tests</div>
      <ul className="results-list">
        {results.map((r, i) => (
          <ResultCard key={`${r.class_path}-${i}`} result={r} index={i} />
        ))}
      </ul>
    </div>
  );
}
