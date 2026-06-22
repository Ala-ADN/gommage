import React, { useMemo, useState } from "react";
import DiffViewer from "./DiffViewer.jsx";
import PromptEditor from "./PromptEditor.jsx";
import StepViewer from "./StepViewer.jsx";

export default function JiraReplayPanel({ record, replayResult, onApplyPromptEdit }) {
  const [selectedStepId, setSelectedStepId] = useState(record.steps[0]?.step_id);
  const selectedStep = useMemo(
    () => record.steps.find((step) => step.step_id === selectedStepId),
    [record.steps, selectedStepId],
  );
  const [promptDraft, setPromptDraft] = useState(selectedStep?.llm?.prompt ?? "");

  return (
    <main className="gommage-panel">
      <aside className="step-list">
        {record.steps.map((step) => (
          <StepViewer
            key={step.step_id}
            step={step}
            selected={step.step_id === selectedStepId}
            onSelect={(stepId) => {
              setSelectedStepId(stepId);
              const nextStep = record.steps.find((item) => item.step_id === stepId);
              setPromptDraft(nextStep?.llm?.prompt ?? "");
            }}
          />
        ))}
      </aside>
      <section className="step-detail">
        {selectedStep?.llm ? (
          <PromptEditor
            prompt={selectedStep.llm.prompt}
            value={promptDraft}
            onChange={setPromptDraft}
            onApply={() => onApplyPromptEdit(selectedStep.step_id, promptDraft)}
          />
        ) : null}
        <DiffViewer divergences={replayResult?.divergences ?? []} />
      </section>
    </main>
  );
}
