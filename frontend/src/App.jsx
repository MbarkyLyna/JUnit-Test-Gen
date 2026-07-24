import { useCallback, useEffect, useRef, useState } from 'react';
import {
  abortGenerateJob,
  analyzeProject,
  checkHealth,
  cloneProject,
  generateTests,
  getGenerateJob,
  startGenerateJob,
  uploadProject,
} from './api/client';
import FileTree from './components/FileTree';
import StatsPanel from './components/StatsPanel';
import UploadZone from './components/UploadZone';

const POLL_INTERVAL_MS = 1500;

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [tree, setTree] = useState(null);
  const [stats, setStats] = useState(null);
  const [analyzed, setAnalyzed] = useState(false);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedPaths, setSelectedPaths] = useState(new Set());
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [results, setResults] = useState([]);
  const [breakdown, setBreakdown] = useState(null);
  const [health, setHealth] = useState({
    ollama_available: false,
    model: 'qwen2.5-coder:1.5b',
    ram_ok: true,
    ram_message: '',
    available_ram_gb: null,
  });
  const [activeJob, setActiveJob] = useState(null);
  const [jobProgress, setJobProgress] = useState(null);
  const pollRef = useRef(null);

  const refreshHealth = useCallback(async () => {
    try {
      const h = await checkHealth();
      setHealth(h);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  const applyImportResult = useCallback((data, message) => {
    setSessionId(data.session_id);
    setTree(data.tree);
    setStats(data.stats);
    setAnalyzed(false);
    setSelectedClass(null);
    setSelectedPaths(new Set());
    setResults([]);
    setBreakdown(null);
    setActiveJob(null);
    setJobProgress(null);
    setStatus(message);
  }, []);

  const handleUpload = useCallback(async (file) => {
    setLoading(true);
    setStatus('Uploading and parsing project…');
    try {
      const data = await uploadProject(file);
      applyImportResult(data, 'Upload complete. Run analysis to compute coverage.');
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [applyImportResult]);

  const handleClone = useCallback(async (repoUrl) => {
    setLoading(true);
    setStatus('Cloning repository…');
    try {
      const data = await cloneProject(repoUrl);
      applyImportResult(data, 'Clone complete. Run analysis to compute coverage.');
    } catch (e) {
      setStatus(`Clone error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [applyImportResult]);

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

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyJobResult = useCallback((job) => {
    if (job.stats) setStats(job.stats);
    if (job.results?.length) setResults(job.results);
    if (job.breakdown) setBreakdown(job.breakdown);
    setAnalyzed(true);
  }, []);

  const pollJob = useCallback((jobId) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const job = await getGenerateJob(jobId);
        setJobProgress({
          status: job.status,
          currentIndex: job.current_index,
          total: job.total,
          message: job.message,
          currentClass: job.current_class,
        });
        setStatus(job.message || 'Generating tests…');

        if (['completed', 'aborted', 'failed'].includes(job.status)) {
          stopPolling();
          setLoading(false);
          setActiveJob(null);
          applyJobResult(job);
          if (job.status === 'completed') {
            const passed = (job.results || []).filter((r) => r.tests_passed).length;
            setStatus(
              `Generated tests for ${job.results?.length ?? 0} class(es). ${passed} passed Maven sandbox verification.`,
            );
          } else if (job.status === 'aborted') {
            setStatus(job.message || 'Generation aborted.');
          } else {
            setStatus(`Generation failed: ${job.error || job.message}`);
          }
        }
      } catch (e) {
        stopPolling();
        setLoading(false);
        setActiveJob(null);
        setStatus(`Job polling error: ${e.message}`);
      }
    }, POLL_INTERVAL_MS);
  }, [stopPolling, applyJobResult]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const handleToggleClass = useCallback((classPath) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(classPath)) next.delete(classPath);
      else next.add(classPath);
      return next;
    });
  }, []);

  const handleGenerateSingle = useCallback(async () => {
    if (!sessionId || !selectedClass) {
      setStatus('Select a Java class from the file tree first.');
      return;
    }
    setLoading(true);
    setStatus(`Generating test for selected class via Ollama (${health.model})…`);
    try {
      const data = await generateTests(sessionId, 'class', selectedClass);
      setStats(data.stats);
      setResults(data.results);
      setBreakdown(data.breakdown);
      setAnalyzed(true);
      const passed = data.results.filter((r) => r.tests_passed).length;
      setStatus(
        `Generated tests for ${data.results.length} class(es). ${passed} passed Maven sandbox verification.`,
      );
    } catch (e) {
      setStatus(`Generation error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }, [sessionId, selectedClass, health.model]);

  const startBatchJob = useCallback(async (scope, classPaths = null) => {
    if (!sessionId) return;

    let currentHealth = health;
    try {
      currentHealth = await checkHealth();
      setHealth(currentHealth);
    } catch {
      /* use cached health */
    }

    if (!currentHealth.ram_ok && (scope === 'classes' || scope === 'project')) {
      setStatus(`Insufficient RAM: ${currentHealth.ram_message}`);
      return;
    }

    setLoading(true);
    setActiveJob(null);
    setJobProgress(null);
    const label = scope === 'project' ? 'full project' : `${classPaths.length} selected class(es)`;
    setStatus(`Starting ${label} generation via Ollama (${currentHealth.model})…`);

    try {
      const { job_id: jobId } = await startGenerateJob(sessionId, scope, classPaths);
      setActiveJob(jobId);
      setJobProgress({ status: 'pending', currentIndex: 0, total: classPaths?.length ?? 0, message: 'Job queued…' });
      pollJob(jobId);
    } catch (e) {
      setLoading(false);
      setStatus(`Generation error: ${e.message}`);
    }
  }, [sessionId, health, pollJob]);

  const handleGenerateSelected = useCallback(async () => {
    const paths = Array.from(selectedPaths);
    if (paths.length === 0) {
      setStatus('Select at least one class using the checkboxes.');
      return;
    }
    await startBatchJob('classes', paths);
  }, [selectedPaths, startBatchJob]);

  const handleGenerateProject = useCallback(async () => {
    await startBatchJob('project');
  }, [startBatchJob]);

  const handleAbort = useCallback(async () => {
    if (!activeJob) return;
    setStatus('Abort requested — stopping after current class…');
    try {
      await abortGenerateJob(activeJob);
    } catch (e) {
      setStatus(`Abort error: ${e.message}`);
    }
  }, [activeJob]);

  const isJobRunning = loading && activeJob;
  const progressPct =
    jobProgress?.total > 0
      ? Math.round((jobProgress.currentIndex / jobProgress.total) * 100)
      : 0;

  return (
    <div className="app">
      <header className="app-header">
        <h1>Spring Test Generator</h1>
        <p className="subtitle">AI-powered JUnit test generation for Spring Boot</p>
      </header>

      <main className="app-main">
        <section className="left-column">
          <UploadZone onUpload={handleUpload} onClone={handleClone} loading={loading} />
          {tree && (
            <>
              <div className="tree-controls panel">
                <div className="panel-header">Selection Mode</div>
                <div className="tree-controls-body">
                  <label className="mode-toggle">
                    <input
                      type="radio"
                      name="selectMode"
                      checked={!multiSelectMode}
                      onChange={() => {
                        setMultiSelectMode(false);
                        setSelectedPaths(new Set());
                      }}
                    />
                    Single class
                  </label>
                  <label className="mode-toggle">
                    <input
                      type="radio"
                      name="selectMode"
                      checked={multiSelectMode}
                      onChange={() => {
                        setMultiSelectMode(true);
                        setSelectedClass(null);
                      }}
                    />
                    Multi-class
                  </label>
                </div>
              </div>
              <FileTree
                tree={tree}
                selectedPath={selectedClass}
                selectedPaths={selectedPaths}
                multiSelect={multiSelectMode}
                onSelectClass={setSelectedClass}
                onToggleClass={handleToggleClass}
              />
            </>
          )}
        </section>

        <section className="right-column">
          <StatsPanel
            stats={stats}
            analyzed={analyzed}
            health={health}
            breakdown={breakdown}
          />

          {sessionId && (
            <div className="actions panel">
              <div className="panel-header">Actions</div>
              <div className="action-buttons">
                <button className="btn secondary" onClick={handleAnalyze} disabled={loading}>
                  Analyze Coverage (Docker)
                </button>
                {!multiSelectMode && (
                  <button
                    className="btn primary"
                    onClick={handleGenerateSingle}
                    disabled={loading || !selectedClass}
                    title={selectedClass ? `Generate for ${selectedClass}` : 'Select a class first'}
                  >
                    Generate for Class
                  </button>
                )}
                {multiSelectMode && (
                  <button
                    className="btn primary"
                    onClick={handleGenerateSelected}
                    disabled={loading || selectedPaths.size === 0}
                  >
                    Generate Selected ({selectedPaths.size})
                  </button>
                )}
                <button
                  className="btn primary"
                  onClick={handleGenerateProject}
                  disabled={loading}
                >
                  Generate for Project
                </button>
                {isJobRunning && (
                  <button className="btn danger" onClick={handleAbort}>
                    Cancel Generation
                  </button>
                )}
              </div>
              {!multiSelectMode && selectedClass && (
                <p className="selected-info">Selected: <code>{selectedClass}</code></p>
              )}
              {multiSelectMode && selectedPaths.size > 0 && (
                <p className="selected-info">{selectedPaths.size} class(es) selected for batch generation</p>
              )}
              {!health.ram_ok && (
                <p className="ram-warning">{health.ram_message}</p>
              )}
            </div>
          )}

          {isJobRunning && jobProgress && (
            <div className="progress panel">
              <div className="panel-header">Generation Progress</div>
              <div className="progress-body">
                <div className="progress-label">
                  {jobProgress.total > 0
                    ? `Generating class ${jobProgress.currentIndex} of ${jobProgress.total}`
                    : jobProgress.message}
                </div>
                {jobProgress.currentClass && (
                  <div className="progress-class">
                    <code>{jobProgress.currentClass}</code>
                  </div>
                )}
                <div className="progress-bar">
                  <div className="progress-fill" style={{ width: `${progressPct}%` }} />
                </div>
                <div className="progress-meta">
                  <span>{jobProgress.message}</span>
                  <span>{progressPct}%</span>
                </div>
              </div>
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
          Static DI resolution, JaCoCo coverage, Ollama LLM ({health.model}).
          Batch generation runs sequentially with cooldown between classes.
        </small>
      </footer>
    </div>
  );
}
