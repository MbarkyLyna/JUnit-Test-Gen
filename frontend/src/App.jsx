import { useCallback, useEffect, useRef, useState } from 'react';
import {
  abortGenerateJob,
  analyzeProject,
  checkHealth,
  cloneProject,
  getGenerateJob,
  getSessionStatus,
  startGenerateJob,
  uploadProject,
} from './api/client';
import FileTree from './components/FileTree';
import GeneratedTestPanel from './components/GeneratedTestPanel';
import StatsPanel from './components/StatsPanel';
import UploadZone from './components/UploadZone';

const JOB_POLL_MS = 1500;
const STATUS_POLL_MS = 2500;

function formatElapsedStatus(baseMessage, elapsedSeconds, dockerActive) {
  if (dockerActive && elapsedSeconds > 0) {
    return `${baseMessage}… ${elapsedSeconds}s elapsed`;
  }
  return `${baseMessage}…`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [tree, setTree] = useState(null);
  const [stats, setStats] = useState(null);
  const [analyzed, setAnalyzed] = useState(false);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedClassPath, setSelectedClassPath] = useState(null);
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
  const statusPollRef = useRef(null);
  const statusBaseRef = useRef('');

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

  const stopStatusPolling = useCallback(() => {
    if (statusPollRef.current) {
      clearInterval(statusPollRef.current);
      statusPollRef.current = null;
    }
  }, []);

  const startStatusPolling = useCallback((sid, baseMessage) => {
    stopStatusPolling();
    statusBaseRef.current = baseMessage;

    const pollDockerStatus = async () => {
      try {
        const s = await getSessionStatus(sid);
        // Only update while a Docker container is active; otherwise leave status
        // to job/analyze handlers (e.g. Ollama generation messages).
        if (s.active) {
          const msg = s.message || baseMessage;
          setStatus(formatElapsedStatus(msg, s.elapsed_seconds, true));
        }
      } catch {
        /* keep last status on poll failure */
      }
    };

    pollDockerStatus();
    statusPollRef.current = setInterval(pollDockerStatus, STATUS_POLL_MS);
  }, [stopStatusPolling]);

  const applyImportResult = useCallback((data, message) => {
    setSessionId(data.session_id);
    setTree(data.tree);
    setStats(data.stats);
    setAnalyzed(false);
    setSelectedClass(null);
    setSelectedClassPath(null);
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
    const baseMsg = 'Running Maven tests inside Docker container sandbox';
    startStatusPolling(sessionId, baseMsg);
    setStatus(`${baseMsg}…`);
    try {
      const data = await analyzeProject(sessionId);
      setStats(data.stats);
      setAnalyzed(true);
      setStatus('Analysis complete.');
    } catch (e) {
      setStatus(`Analysis error: ${e.message}`);
    } finally {
      stopStatusPolling();
      setLoading(false);
    }
  }, [sessionId, startStatusPolling, stopStatusPolling]);

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

  const pollJob = useCallback((jobId, sid) => {
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

        if (job.status === 'running' && sid) {
          try {
            const s = await getSessionStatus(sid);
            const msg = s.active && s.message ? s.message : (job.message || 'Generating tests');
            setStatus(formatElapsedStatus(msg, s.elapsed_seconds, s.active));
          } catch {
            setStatus(job.message || 'Generating tests…');
          }
        } else {
          setStatus(job.message || 'Generating tests…');
        }

        if (['completed', 'aborted', 'failed'].includes(job.status)) {
          stopPolling();
          stopStatusPolling();
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
        stopStatusPolling();
        setLoading(false);
        setActiveJob(null);
        setStatus(`Job polling error: ${e.message}`);
      }
    }, JOB_POLL_MS);
  }, [stopPolling, stopStatusPolling, applyJobResult]);

  useEffect(() => () => {
    stopPolling();
    stopStatusPolling();
  }, [stopPolling, stopStatusPolling]);

  const handleSelectClass = useCallback(({ path, fqcn }) => {
    setSelectedClassPath(path);
    setSelectedClass(fqcn);
  }, []);

  const handleToggleClass = useCallback((classPath) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(classPath)) next.delete(classPath);
      else next.add(classPath);
      return next;
    });
  }, []);

  const startBatchJob = useCallback(async (scope, classPath = null, classPaths = null) => {
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
    const label =
      scope === 'project'
        ? 'full project'
        : scope === 'class'
          ? `class ${classPath}`
          : `${classPaths?.length ?? 0} selected class(es)`;
    const baseMsg = `Starting ${label} generation via Ollama (${currentHealth.model})`;
    startStatusPolling(sessionId, 'Running Maven tests inside Docker container sandbox');
    setStatus(`${baseMsg}…`);

    try {
      const { job_id: jobId } = await startGenerateJob(sessionId, scope, classPath, classPaths);
      setActiveJob(jobId);
      setJobProgress({
        status: 'pending',
        currentIndex: 0,
        total: scope === 'class' ? 1 : (classPaths?.length ?? 0),
        message: 'Job queued…',
      });
      pollJob(jobId, sessionId);
    } catch (e) {
      stopStatusPolling();
      setLoading(false);
      setStatus(`Generation error: ${e.message}`);
    }
  }, [sessionId, health, pollJob, startStatusPolling, stopStatusPolling]);

  const handleGenerateSingle = useCallback(async () => {
    if (!sessionId || !selectedClass) {
      setStatus('Select a Java class from the file tree first.');
      return;
    }
    await startBatchJob('class', selectedClass);
  }, [sessionId, selectedClass, startBatchJob]);

  const handleGenerateSelected = useCallback(async () => {
    const paths = Array.from(selectedPaths);
    if (paths.length === 0) {
      setStatus('Select at least one class using the checkboxes.');
      return;
    }
    await startBatchJob('classes', null, paths);
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
                        setSelectedClassPath(null);
                      }}
                    />
                    Multi-class
                  </label>
                </div>
              </div>
              <FileTree
                tree={tree}
                selectedPath={selectedClassPath}
                selectedPaths={selectedPaths}
                multiSelect={multiSelectMode}
                onSelectClass={handleSelectClass}
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
                <p className="selected-info">
                  Selected: <code>{selectedClass}</code>
                </p>
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

          <GeneratedTestPanel results={results} />
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
