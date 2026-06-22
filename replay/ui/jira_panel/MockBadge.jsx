export default function MockBadge({ mocked, sideEffecting }) {
  const label = mocked ? "Mocked" : sideEffecting ? "Side effect" : "Live-safe";
  const className = mocked ? "badge badge-mocked" : sideEffecting ? "badge badge-risk" : "badge";
  return <span className={className}>{label}</span>;
}
