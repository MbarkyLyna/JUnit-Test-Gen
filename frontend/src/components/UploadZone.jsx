import { useRef, useState } from 'react';

export default function UploadZone({ onUpload, onClone, loading }) {
  const inputRef = useRef(null);
  const [githubUrl, setGithubUrl] = useState('');
  const [mode, setMode] = useState('zip');

  const handleChange = (e) => {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
  };

  const handleClone = () => {
    if (githubUrl.trim()) onClone(githubUrl.trim());
  };

  return (
    <div className="upload-zone panel">
      <div className="panel-header">Import Spring Boot Project</div>

      <div className="input-mode-tabs">
        <button
          type="button"
          className={`mode-tab ${mode === 'zip' ? 'active' : ''}`}
          onClick={() => setMode('zip')}
        >
          ZIP Upload
        </button>
        <button
          type="button"
          className={`mode-tab ${mode === 'github' ? 'active' : ''}`}
          onClick={() => setMode('github')}
        >
          GitHub Clone
        </button>
      </div>

      {mode === 'zip' ? (
        <div className="upload-body">
          <p className="upload-hint">Upload a Maven project as a .zip file</p>
          <input ref={inputRef} type="file" accept=".zip" onChange={handleChange} hidden />
          <button
            className="btn primary"
            onClick={() => inputRef.current?.click()}
            disabled={loading}
          >
            {loading ? 'Importing…' : 'Choose ZIP file'}
          </button>
        </div>
      ) : (
        <div className="upload-body">
          <p className="upload-hint">Paste a public GitHub repository URL</p>
          <input
            type="url"
            className="text-input"
            placeholder="https://github.com/owner/repo"
            value={githubUrl}
            onChange={(e) => setGithubUrl(e.target.value)}
            disabled={loading}
          />
          <button
            className="btn primary"
            onClick={handleClone}
            disabled={loading || !githubUrl.trim()}
          >
            {loading ? 'Cloning…' : 'Clone Repository'}
          </button>
        </div>
      )}
    </div>
  );
}
