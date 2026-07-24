import { useState } from 'react';

function isSelectableJava(node) {
  return (
    node.name.endsWith('.java') &&
    !node.path.includes('/test/') &&
    !node.path.includes('\\test\\') &&
    (node.path.includes('/main/') || node.path.includes('\\main\\') || !node.path.includes('test'))
  );
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
  const isSelected = !multiSelect && node.path === selectedPath;
  const isChecked = multiSelect && selectedPaths.has(node.path);

  const handleRowClick = () => {
    if (isDir) {
      setExpanded((e) => !e);
    } else if (isJava) {
      if (multiSelect) {
        onToggleCheck(node.path);
      } else {
        onSelect(node.path);
      }
    }
  };

  const handleCheckbox = (e) => {
    e.stopPropagation();
    if (isJava) onToggleCheck(node.path);
  };

  return (
    <div className="tree-node">
      <div
        className={`tree-row ${isSelected ? 'selected' : ''} ${isChecked ? 'checked' : ''} ${isJava ? 'selectable' : ''}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleRowClick}
      >
        {multiSelect && isJava && (
          <input
            type="checkbox"
            className="tree-checkbox"
            checked={isChecked}
            onChange={handleCheckbox}
            onClick={(e) => e.stopPropagation()}
          />
        )}
        <span className={`tree-icon ${isDir ? 'icon-folder' : isJava ? 'icon-java' : 'icon-file'}`} />
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
