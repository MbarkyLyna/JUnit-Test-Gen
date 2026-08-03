const API_BASE = '/api';

async function parseError(res, fallback) {
  const err = await res.json().catch(() => ({}));
  const detail = err.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  return fallback;
}

export async function uploadProject(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/upload`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await parseError(res, 'Upload failed'));
  return res.json();
}

export async function cloneProject(repoUrl, branch = null) {
  const res = await fetch(`${API_BASE}/clone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_url: repoUrl, branch }),
  });
  if (!res.ok) throw new Error(await parseError(res, 'Clone failed'));
  return res.json();
}

export async function analyzeProject(sessionId) {
  const res = await fetch(`${API_BASE}/analyze/${sessionId}`, { method: 'POST' });
  if (!res.ok) throw new Error(await parseError(res, 'Analysis failed'));
  return res.json();
}

export async function generateTests(sessionId, scope, classPath = null, classPaths = null) {
  const body = { scope, class_path: classPath };
  if (classPaths) body.class_paths = classPaths;
  const res = await fetch(`${API_BASE}/generate/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res, 'Generation failed'));
  return res.json();
}

export async function getSessionStatus(sessionId) {
  const res = await fetch(`${API_BASE}/status/${sessionId}`);
  if (!res.ok) throw new Error(await parseError(res, 'Failed to fetch session status'));
  return res.json();
}

export async function startGenerateJob(sessionId, scope, classPath = null, classPaths = null) {
  const body = { scope };
  if (classPath) body.class_path = classPath;
  if (classPaths) body.class_paths = classPaths;
  const res = await fetch(`${API_BASE}/generate/${sessionId}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res, 'Failed to start generation job'));
  return res.json();
}

export async function getGenerateJob(jobId) {
  const res = await fetch(`${API_BASE}/generate/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res, 'Failed to fetch job status'));
  return res.json();
}

export async function abortGenerateJob(jobId) {
  const res = await fetch(`${API_BASE}/generate/jobs/${jobId}/abort`, { method: 'POST' });
  if (!res.ok) throw new Error(await parseError(res, 'Failed to abort job'));
  return res.json();
}

export async function checkHealth() {
  const res = await fetch(`${API_BASE}/health`);
  return res.json();
}
