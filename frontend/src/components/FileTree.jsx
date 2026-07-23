import { useState } from 'react';

const ICONS = {
  directory: '📁',
  directoryOpen: '📂',
  file: '📄',
  java: '☕',
};

function nodeIcon(node, expanded) {
  if (node.type === 'directory') return expanded ? ICONS.directoryOpen : ICONS.directory;
  if (node.name.endsWith('.java')) return ICONS.java;
  return ICONS.file;
}

function TreeNode({ node, depth, selectedPath, onSelect }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const isDir = node.type === 'directory';
  const isSelected = node.path === selectedPath;
  const isJava =
    node.name.endsWith('.java') &&
    !node.path.includes('/test/') &&
    !node.path.includes('\\test\\') &&
    (node.path.includes('/main/') || node.path.includes('\\main\\') || !node.path.includes('test'));

  const handleClick = () => {
    if (isDir) {
      setExpanded((e) => !e);
    } else if (isJava) {
      onSelect(node.path);
    }
  };

  return (
    <div className="tree-node">
      <div
        className={`tree-row ${isSelected ? 'selected' : ''} ${isJava ? 'selectable' : ''}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
      >
        <span className="tree-icon">{nodeIcon(node, expanded)}</span>
        <span className="tree-label">{node.name}</span>
      </div>
      {isDir && expanded && node.children?.map((child) => (
        <TreeNode
          key={child.path || child.name}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

export default function FileTree({ tree, selectedPath, onSelectClass }) {
  if (!tree) return null;

  return (
    <div className="file-tree panel">
      <div className="panel-header">Project Explorer</div>
      <div className="tree-body">
        <TreeNode node={tree} depth={0} selectedPath={selectedPath} onSelect={onSelectClass} />
      </div>
    </div>
  );
}
