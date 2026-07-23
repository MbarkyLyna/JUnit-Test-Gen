import { useCallback, useEffect, useState } from 'react';
import { analyzeProject, checkHealth, generateTests, uploadProject } from './api/client';
import FileTree from './components/FileTree';
import StatsPanel from './components/StatsPanel';
import UploadZone from './components/UploadZone';

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [tree, setTree] = useState(null);
  const [stats, setStats] = useState(null);
  const [analyzed, setAnalyzed] = useState(false);
  const [selectedClass, setSelectedClass] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [results, setResults] = useState([]);
  const [breakdown, setBreakdown] = useState(null);
  const [ollamaOk, setOllamaOk] = useState(false);

  useEffect(() => {
    checkHealth().then((h) => setOllamaOk(h.ollama_available)).catch(() => {});
  }, []);

  const handleUpload = useCallback(async (file) => {
    setLoading(true);
    setStatus('Uploading and parsing project…');
    try {
      const data = await uploadProject(file);
      setSessionId(data.session_id);
      setTree(data.tree);
      setStats(data.stats);
      setAnalyzed(false);
      setSelectedClass(null);
      setResults([]);
      setBreakdown(null);
      setStatus('Upload complete. Run analysis to compute coverage.');
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleAnalyze = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setStatus('Running Maven tests inside Docker container sandbox and JaCoCo analysis…');
    try {
      const data = await analyzeProject(sessionId);
      setStats(data.stats);
      setAnalyzed(true);
      setStatus('Analysis complete.');
    } catch (e) {
      setStatus(`Analysis error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const handleGenerate = useCallback(async (scope) => {
    if (!sessionId) return;
    if (scope === 'class' && !selectedClass) {
      setStatus('Select a Java class from the file tree first.');
      return;
    }
    setLoading(true);
    setStatus(`Generating tests (${scope}) via Ollama (phi4-mini)…`);
    try {
      const data = await generateTests(sessionId, scope, selectedClass);
      setStats(data.stats);
      setResults(data.results);
      setBreakdown(data.breakdown);
      setAnalyzed(true);
      const passed = data.results.filter((r) => r.tests_passed).length;
      setStatus(`Generated tests for ${data.results.length} class(es). ${passed} passed Maven sandbox verification.`);
    } catch (e) {
      setStatus(`Generation error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [sessionId, selectedClass]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Spring Test Generator</h1>
        <p className="subtitle">AI-powered JUnit test generation for Spring Boot (MVP)</p>
      </header>

      <main className="app-main">
        <section className="left-column">
          <UploadZone onUpload={handleUpload} loading={loading} />
          {tree && (
            <FileTree
              tree={tree}
              selectedPath={selectedClass}
              onSelectClass={setSelectedClass}
            />
          )}
        </section>

        <section className="right-column">
          <StatsPanel stats={stats} analyzed={analyzed} ollamaOk={ollamaOk} breakdown={breakdown} />

          {sessionId && (
            <div className="actions panel">
              <div className="panel-header">Actions</div>
              <div className="action-buttons">
                <button className="btn secondary" onClick={handleAnalyze} disabled={loading}>
                  Analyze Coverage (Docker)
                </button>
                <button
                  className="btn primary"
                  onClick={() => handleGenerate('class')}
                  disabled={loading || !selectedClass}
                  title={selectedClass ? `Generate for ${selectedClass}` : 'Select a class first'}
                >
                  Generate for Class
                </button>
                <button
                  className="btn primary"
                  onClick={() => handleGenerate('project')}
                  disabled={loading}
                >
                  Generate for Project
                </button>
              </div>
              {selectedClass && (
                <p className="selected-info">Selected: <code>{selectedClass}</code></p>
              )}
            </div>
          )}

          {status && (
            <div className="status panel">
              <div className="panel-header">Status</div>
              <p>{status}</p>
            </div>
          )}

          {results.length > 0 && (
            <div className="results panel">
              <div className="panel-header">Per-Class Generation Breakdown</div>
              <ul className="results-list">
                {results.map((r, i) => (
                  <li key={i} className={`result-card ${r.status}`}>
                    <div className="result-top">
                      <strong className="class-name">{r.class_name || r.class_path}</strong>
                      <span className={`status-pill ${r.status}`}>
                        {r.status === 'full_coverage' && 'Full Coverage (≥80%)'}
                        {r.status === 'partial_coverage' && 'Partial Coverage'}
                        {r.status === 'failed_to_compile' && 'Failed to Compile'}
                        {r.status === 'no_change' && 'No Coverage Delta'}
                      </span>
                    </div>
                    <div className="result-paths">
                      <span className="src-path">{r.class_path}</span>
                      {r.test_path && <span className="arrow"> → {r.test_path}</span>}
                    </div>
                    <div className="result-metrics">
                      <span>Initial: {r.initial_coverage_pct}%</span>
                      <span>Final: {r.final_coverage_pct}%</span>
                      <span className={r.coverage_delta > 0 ? 'delta-pos' : 'delta-zero'}>
                        Delta: {r.coverage_delta > 0 ? `+${r.coverage_delta}%` : '0%'}
                      </span>
                    </div>
                    <div className="result-msg">{r.message}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </main>

      <footer className="app-footer">
        <small>
          MVP scope: static DI resolution, JaCoCo coverage, Ollama LLM.
          Post-MVP: mutation testing, advanced DI, baseline comparisons.
        </small>
      </footer>
    </div>
  );
}
