export default function DiffViewer({ divergences = [] }) {
  if (!divergences.length) {
    return <p className="empty-state">No divergences detected.</p>;
  }
  return (
    <ol className="diff-list">
      {divergences.map((item) => (
        <li key={`${item.step_id}-${item.field}`}>
          <strong>Step {item.step_id}</strong>
          <span>{item.field}</span>
          <code>{JSON.stringify(item.modified)}</code>
        </li>
      ))}
    </ol>
  );
}
