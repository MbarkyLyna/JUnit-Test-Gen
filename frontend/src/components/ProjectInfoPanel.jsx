export default function ProjectInfoPanel({ info }) {
  if (!info) return null;

  const coordinates = [info.group_id, info.artifact_id, info.version]
    .filter(Boolean)
    .join(':');

  return (
    <div className="project-info panel">
      <div className="panel-header">
        Project Info
        {!info.has_pom && <span className="panel-badge warn-badge">No pom.xml</span>}
      </div>
      <div className="project-info-grid">
        <div className="info-item">
          <span className="info-label">Maven coordinates</span>
          <span className="info-value">{coordinates || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Packaging</span>
          <span className="info-value">{info.packaging || '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">Java version</span>
          <span className="info-value">{info.java_version ?? '—'}</span>
        </div>
        <div className="info-item">
          <span className="info-label">JaCoCo version</span>
          <span className="info-value">{info.jacoco_version || '—'}</span>
        </div>
        <div className="info-item info-item-wide">
          <span className="info-label">Sandbox image</span>
          <span className="info-value">
            <code>{info.docker_image || '—'}</code>
          </span>
        </div>
      </div>
    </div>
  );
}