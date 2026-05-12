# Codebase Investigator

A web app that lets you paste any public GitHub URL and ask plain-English questions about the code. Every answer is grounded in specific files and line numbers — and independently audited by a second model call that verifies citations, checks for overclaims, and flags contradictions with what was established in prior turns.

---

## Features

- **Code-grounded answers** — the investigator reads actual files before making any claim; every fact is cited as `path/file.ext:start_line-end_line`
- **Independent audit on every response** — a separate model call (different system prompt, different context) verifies each citation against the real file content and assesses reasoning quality
- **Multi-turn coherence** — a claims log tracks established facts across all turns; the auditor checks new answers for contradictions with prior ones
- **Real-time tool activity** — watch the model call `get_structure`, `search_code`, `read_file` as it investigates
- **Citation viewer** — click any citation chip in an answer to open the exact lines in a modal
- **Audit history** — trust-level timeline (HIGH / MEDIUM / LOW / UNRELIABLE) across all turns of a session

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI |
| LLM | Groq API (`llama-3.1-8b-instant`) |
| Agentic loop | Custom ReAct — model outputs `<tool_call>` tags, server parses and executes |
| Streaming | Server-Sent Events (SSE) via FastAPI `StreamingResponse` |
| Repo cloning | GitPython (shallow clone, depth=1) |
| Code search | `grep` (subprocess) with Python fallback |
| Frontend | Vanilla JS + HTML/CSS, [marked.js](https://marked.js.org/) for markdown |
| Server | Uvicorn (ASGI) |

No database. No framework on the frontend. Sessions are in-memory.

---

## How It Works

```
User pastes GitHub URL
        │
        ▼
  Server shallow-clones repo → temp dir
        │
        ▼
  User asks a question
        │
        ▼
┌─────────────────────────────┐
│       INVESTIGATOR          │  ← llama-3.1-8b-instant
│                             │
│  ReAct loop (up to 6 steps) │
│  ┌──────────────────────┐   │
│  │ Model outputs        │   │
│  │ <tool_call>{...}     │   │
│  │ </tool_call>         │   │
│  └──────────┬───────────┘   │
│             │               │
│  Server parses + executes:  │
│  • get_structure()          │
│  • search_code()            │
│  • read_file()              │
│  • list_files()             │
│             │               │
│  Result fed back as text    │
│  → model continues          │
└─────────────┬───────────────┘
              │ Final answer (with `file:N-M` citations)
              ▼
┌─────────────────────────────┐
│         AUDITOR             │  ← Same model, independent call
│                             │
│  1. Extract citations       │
│  2. Programmatically verify │  ← reads actual file at cited lines
│     each one against repo   │
│  3. Check for overclaims,   │
│     gaps, reasoning holes   │
│  4. Cross-check against     │
│     prior claims log        │
│  5. Return structured JSON  │
│     trust report            │
└─────────────────────────────┘
              │
              ▼
  Answer + Audit shown in UI
  New claims logged for next turn
```

The audit is always a **separate** API call with a different system prompt. "I rate my confidence 8/10" from the same call that produced the answer is noise — the audit has to come from somewhere else.

---

## Installation

### Prerequisites

- Python 3.12+
- `git` (for cloning repos)
- A free [Groq API key](https://console.groq.com) — sign up, no credit card required

### Steps

```bash
# 1. Clone this repo
git clone <repo-url>
cd codebase_investigator

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your Groq API key
cp env.example env
# Edit env and set: GROQ_API_KEY=gsk_your_key_here

# 4. Run
bash run.sh
# or directly:
python3.12 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in your browser.

### Dependencies

```
groq>=1.2.0          # Groq SDK (LLM API)
fastapi>=0.115.0     # Web framework
uvicorn[standard]    # ASGI server
gitpython>=3.1.40    # GitHub repo cloning
python-dotenv>=1.0.0 # .env loading
```

---

## Usage

1. Paste a public GitHub URL (e.g. `https://github.com/pallets/flask`)
2. Wait for the repo to clone (~5–15 seconds depending on repo size)
3. Ask anything about the code

### Example questions

```
How does auth work here, and what would you change about it?
This signup flow feels off — walk me through it and flag anything risky.
Is there dead code? What's safe to delete?
Why is this function async? Does it need to be?
Suggest a better way to handle errors in the API layer.
Walk me through what this service does. Skip the obvious.
```

The investigator will:
- Call `get_structure` to understand the repo layout
- Use `search_code` to find relevant entry points
- Read specific file sections with `read_file`
- Return a cited answer

The audit panel on the right shows the trust level, citation check results, and any issues found.

---

## Project Structure

```
codebase_investigator/
├── main.py           # FastAPI app — routes, SSE streaming
├── investigator.py   # ReAct agent loop (tool calling + answer generation)
├── auditor.py        # Independent audit agent (citation verification + LLM review)
├── tools.py          # CodebaseTools — read_file, search_code, list_files, get_structure
├── session.py        # In-memory session store, conversation history, claims log
├── github_utils.py   # Shallow-clone public GitHub repos
├── requirements.txt
├── run.sh
├── .env.example
└── static/
    ├── index.html    # Single-page UI
    ├── styles.css    # Dark theme, two-column layout
    └── app.js        # SSE parsing, tool activity, audit rendering, citation modal
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the web UI |
| `POST` | `/api/clone` | Clone a GitHub repo, create session |
| `POST` | `/api/ask/{session_id}` | Ask a question — returns SSE stream |
| `GET` | `/api/file/{session_id}` | Read file content (for citation modal) |
| `GET` | `/api/session/{session_id}` | Session info (turn count, claims count) |

### SSE Event Types

Events streamed from `/api/ask`:

| Event | Payload | Description |
|---|---|---|
| `tool_start` | `{name}` | Model is about to call a tool |
| `tool_execute` | `{name, input}` | Tool called with these arguments |
| `tool_done` | `{name, preview}` | Tool result received |
| `answer_complete` | `{text}` | Final answer ready |
| `audit` | audit JSON | Independent audit report |
| `complete` | `{}` | Turn finished |
| `error` | `{message}` | Something went wrong |

---

## Limitations

- **Public repos only** — no auth for private repos
- **Free Groq tier** — `llama-3.1-8b-instant` has 30k tokens/min; very large repos with dense investigations may hit rate limits
- **In-memory sessions** — sessions are lost on server restart
- **Shallow clones** — depth=1, so git history is not available to the tools
- **No binary files** — images, compiled files, and lock files are skipped

---

## License

MIT
