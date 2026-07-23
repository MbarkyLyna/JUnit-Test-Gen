import { useRef } from 'react';

export default function UploadZone({ onUpload, loading }) {
  const inputRef = useRef(null);

  const handleChange = (e) => {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
  };

  return (
    <div className="upload-zone panel">
      <div className="panel-header">Upload Spring Boot Project</div>
      <p className="upload-hint">Upload a Maven project as a .zip file</p>
      <input
        ref={inputRef}
        type="file"
        accept=".zip"
        onChange={handleChange}
        hidden
      />
      <button
        className="btn primary"
        onClick={() => inputRef.current?.click()}
        disabled={loading}
      >
        {loading ? 'Uploading…' : 'Choose ZIP file'}
      </button>
    </div>
  );
}
