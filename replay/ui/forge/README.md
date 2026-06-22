# Gommage Jira Forge Panel

This app embeds Gommage directly in Jira as a `jira:issuePanel`.

## What It Does

- Opens from a Jira issue as `Gommage Replay`.
- Reads the current Jira issue key from Forge context.
- Records a Gommage trace for that issue through the configured backend.
- Attaches the AER JSON trace to the Jira issue.
- Replays in debug mode with side-effecting tool calls mocked.
- Lets a developer rewrite prompts or inject tool results.
- Creates a linked Jira fix issue and attaches the trace as evidence.

## Local Setup

Run the Python backend and expose it through HTTPS:

```bash
cd /mnt/data/home/adem/Desktop/gommage
source .venv/bin/activate
python main.py ui --host 0.0.0.0 --port 8010
ngrok http 8010
```

Then configure and deploy the Forge app:

```bash
cd replay/ui/forge
npm install
npm run build
forge register
forge variables set GOMMAGE_BACKEND_URL https://YOUR-TUNNEL.ngrok-free.app
forge deploy
forge install --product jira
```

Open a Jira issue, click the `Gommage Replay` issue panel button, then use:

1. `Record current issue`
2. `Replay in Debug Mode`
3. Rewrite a prompt or inject a tool result
4. `Create linked fix issue`

The manifest allows common HTTPS tunnel domains for demo use. For production,
replace those egress permissions with your real Gommage backend domain.
