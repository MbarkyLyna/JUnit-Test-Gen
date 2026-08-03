import { useState } from 'react';

function pathToFqcn(filePath) {
  const normalized = filePath.replace(/\\/g, '/');
  const match = normalized.match(/(?:^|\/)src\/main\/java\/(.+)\.java$/);
  if (!match) return null;
  return match[1].replace(/\//g, '.');
}

function isSelectableJava(node) {
  return (
    node.name.endsWith('.java') &&
    !node.path.includes('/test/') &&
    !node.path.includes('\\test\\') &&
    (node.path.includes('/main/') || node.path.includes('\\main\\') || !node.path.includes('test'))
  );
}

function fileIconClass(name, isDir) {
  if (isDir) return 'icon-folder';
  const lower = name.toLowerCase();
  if (lower.endsWith('.java')) return 'icon-java';
  if (lower.endsWith('.yml') || lower.endsWith('.yaml')) return 'icon-yaml';
  if (lower.endsWith('.xml')) return 'icon-xml';
  if (lower.endsWith('.properties')) return 'icon-props';
  if (lower.endsWith('.json')) return 'icon-json';
  if (lower.endsWith('.md')) return 'icon-md';
  return 'icon-file';
}

function TreeNode({
  node,
  depth,
  selectedPath,
  selectedPaths,
  multiSelect,
  onSelect,
  onToggleCheck,
}) {
  const [expanded, setExpanded] = useState(depth < 2);
  const isDir = node.type === 'directory';
  const isJava = isSelectableJava(node);
  const isSelected = !multiSelect && isJava && node.path === selectedPath;
  const isChecked = multiSelect && selectedPaths.has(node.path);
  const iconClass = fileIconClass(node.name, isDir);

  const handleRowClick = () => {
    if (isDir) {
      setExpanded((e) => !e);
      return;
    }
    if (!isJava) return;
    if (multiSelect) {
      onToggleCheck(node.path);
    } else {
      const fqcn = pathToFqcn(node.path);
      onSelect({ path: node.path, fqcn: fqcn || node.path });
    }
  };

  const handleChevronClick = (e) => {
    e.stopPropagation();
    setExpanded((v) => !v);
  };

  const handleCheckbox = (e) => {
    e.stopPropagation();
    if (isJava) onToggleCheck(node.path);
  };

  return (
    <div className="tree-node">
      <div
        className={[
          'tree-row',
          isSelected ? 'selected' : '',
          isChecked ? 'checked' : '',
          isJava && !multiSelect ? 'selectable' : '',
          isJava && multiSelect ? 'multi-selectable' : '',
          isDir ? 'is-directory' : '',
        ].filter(Boolean).join(' ')}
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
        onClick={handleRowClick}
        title={isJava && !multiSelect ? pathToFqcn(node.path) || node.path : node.path}
      >
        <span
          className={`tree-chevron ${isDir ? (expanded ? 'expanded' : '') : 'hidden'}`}
          onClick={isDir ? handleChevronClick : undefined}
          aria-hidden="true"
        />
        {multiSelect && isJava && (
          <input
            type="checkbox"
            className="tree-checkbox"
            checked={isChecked}
            onChange={handleCheckbox}
            onClick={(e) => e.stopPropagation()}
          />
        )}
        <span className={`tree-icon ${iconClass} ${isDir && expanded ? 'open' : ''}`} />
        <span className="tree-label">{node.name}</span>
      </div>
      {isDir && expanded && node.children?.map((child) => (
        <TreeNode
          key={child.path || child.name}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          selectedPaths={selectedPaths}
          multiSelect={multiSelect}
          onSelect={onSelect}
          onToggleCheck={onToggleCheck}
        />
      ))}
    </div>
  );
}

export default function FileTree({
  tree,
  selectedPath,
  selectedPaths,
  multiSelect,
  onSelectClass,
  onToggleClass,
}) {
  if (!tree) return null;

  return (
    <div className="file-tree panel">
      <div className="panel-header">
        Project Explorer
        {multiSelect && selectedPaths.size > 0 && (
          <span className="panel-badge">{selectedPaths.size} selected</span>
        )}
      </div>
      <div className="tree-body">
        <TreeNode
          node={tree}
          depth={0}
          selectedPath={selectedPath}
          selectedPaths={selectedPaths}
          multiSelect={multiSelect}
          onSelect={onSelectClass}
          onToggleCheck={onToggleClass}
        />
      </div>
    </div>
  );
}

export { pathToFqcn };
