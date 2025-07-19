# decider

Sample code - leverages the AI agent "browser-use" to accept or reject LinkedIn invitations.

Set the following environment variables before running:

- `OPENAI_API_KEY` – API key used for OpenAI requests.
- `BRAVE_BROWSER_PATH` – path to your Brave browser executable (defaults to macOS location if unset).

To scan pending invitations without taking action, run:

```bash
python spotter.py
```

This lists profiles that scored as extremist based on their recent activity.
