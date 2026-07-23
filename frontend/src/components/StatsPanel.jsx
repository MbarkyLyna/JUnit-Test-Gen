export default function StatsPanel({ stats, analyzed, ollamaOk, breakdown }) {
  const staticStats = stats?.static || {};
  const coverage = stats?.coverage;

  return (
    <div className="stats-panel panel">
      <div className="panel-header">Project Statistics</div>
      <div className="stats-grid">
        <div className="stat-card">
          <span className="stat-value">{staticStats.class_count ?? 0}</span>
          <span className="stat-label">Classes</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{staticStats.package_count ?? 0}</span>
          <span className="stat-label">Packages</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{staticStats.test_count ?? 0}</span>
          <span className="stat-label">@Test methods</span>
        </div>
        <div className="stat-card highlight">
          <span className="stat-value">
            {analyzed && coverage ? `${coverage.line_coverage_pct}%` : '—'}
          </span>
          <span className="stat-label">Line coverage (JaCoCo)</span>
        </div>
      </div>

      {breakdown && (
        <div className="breakdown-summary">
          <div className="panel-subhead">Generation Breakdown</div>
          <div className="breakdown-grid">
            <div className="break-card success">
              <span className="break-val">{breakdown.full_coverage_count}</span>
              <span className="break-lbl">Full (≥80%)</span>
            </div>
            <div className="break-card warning">
              <span className="break-val">{breakdown.partial_coverage_count}</span>
              <span className="break-lbl">Partial</span>
            </div>
            <div className="break-card danger">
              <span className="break-val">{breakdown.failed_compile_count}</span>
              <span className="break-lbl">Compile Error</span>
            </div>
          </div>
          <p className="headline-text">{breakdown.summary_headline}</p>
        </div>
      )}

      {analyzed && coverage && (
        <div className="coverage-bar-wrap">
          <div className="coverage-bar">
            <div
              className="coverage-fill"
              style={{ width: `${Math.min(coverage.line_coverage_pct, 100)}%` }}
            />
          </div>
          <span className="coverage-target">Target: 80%</span>
        </div>
      )}
      <div className={`status-badge ${ollamaOk ? 'ok' : 'warn'}`}>
        Ollama: {ollamaOk ? 'Connected (phi4-mini)' : 'Not available (localhost:11434)'}
      </div>
    </div>
  );
}

