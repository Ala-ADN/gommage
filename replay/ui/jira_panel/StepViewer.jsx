import MockBadge from "./MockBadge.jsx";

export default function StepViewer({ step, selected, onSelect }) {
  const tool = step.tool;
  const llm = step.llm;
  return (
    <button
      className={`step-row ${selected ? "selected" : ""}`}
      type="button"
      onClick={() => onSelect(step.step_id)}
    >
      <span className="step-id">#{step.step_id}</span>
      <span className="step-kind">{step.kind}</span>
      <span className="step-intent">{step.intent}</span>
      {tool ? <MockBadge mocked={tool.mocked} sideEffecting={tool.side_effecting} /> : null}
      {llm ? <span className="step-model">{llm.model}</span> : null}
    </button>
  );
}
