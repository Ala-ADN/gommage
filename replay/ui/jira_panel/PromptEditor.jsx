export default function PromptEditor({ prompt, value, onChange, onApply }) {
  return (
    <form className="prompt-editor" onSubmit={(event) => { event.preventDefault(); onApply(); }}>
      <label htmlFor="prompt-editor">Prompt</label>
      <textarea
        id="prompt-editor"
        value={value ?? prompt ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
      <button type="submit">Apply edit</button>
    </form>
  );
}
