# ⚡ PipelineIQ — Internal GPT for CI/CD Failure Intelligence

> Chat with your CI/CD failures like a senior DevOps engineer is on call 24/7.
> **100% local. No API keys. No cloud. Runs on your machine.**

---

## How It Works

```
Paste / pipe a failure log
         ↓
   Log Chunker
   (split by stage, extract error context)
         ↓
   Sentence Transformers
   (local embeddings, ~80MB, no API)
         ↓
   ChromaDB  ←──────────────────────────────────────┐
   (persistent local vector store)                  │
         ↓                                           │
   RAG Retrieval                              record_fix()
   (find similar past failures)              teaches the system
         ↓
   Ollama (llama3 / mistral / codellama)
   (local LLM, no API key)
         ↓
   Root Cause + Fix + Confidence + Chat
```

---

## Prerequisites

### 1. Install Ollama
```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download from https://ollama.com/download
```

### 2. Pull a model
```bash
ollama pull llama3          # Recommended — best overall
ollama pull mistral         # Faster, lighter
ollama pull codellama       # Best for code-heavy logs
```

### 3. Start Ollama
```bash
ollama serve
```

### 4. Install Python dependencies
```bash
pip install -r requirements.txt
```

---

## Usage

### Interactive Mode (Internal GPT Chat)
```bash
python cli.py
```

You'll get an interactive prompt where you can:
- Paste logs and get instant analysis
- Ask follow-up questions
- Record resolved failures to improve future answers

```
pipelineiq › analyze
Pipeline type [github-actions]: github-actions
Paste your failure log (type END when done):
... paste log ...
END

  ◆  ROOT CAUSE ANALYSIS
  ────────────────────────────────────────
  ⚠  Root Cause:  NPM peer dependency conflict between React 18 and
                  @legacy-component requiring React 17

  Stage:        build
  Error Type:   DependencyConflict
  Confidence:   High

  ◆  THE FIX
  ────────────────────────────────────────
  Add --legacy-peer-deps flag to npm ci

  1.  Open your .github/workflows/ci.yml
  2.  Find the `npm ci` step
  3.  Change to: npm ci --legacy-peer-deps

  Run:  $ npm ci --legacy-peer-deps

pipelineiq › How do I prevent this permanently?
  PipelineIQ  Lock peer dependencies in package.json using exact versions...
```

### Analyze a log file directly
```bash
python cli.py --log ./logs/build-failure.log
python cli.py --log ./logs/build-failure.log --pipe terraform
python cli.py --log ./logs/build-failure.log --json   # Raw JSON output
```

### Switch models
```bash
python cli.py --model mistral
python cli.py --model codellama
```

### Record a resolved failure (teaches the system)
```bash
python cli.py --teach
```

### Show knowledge base stats
```bash
python cli.py --stats
```

---

## REST API (Optional)

If you want to integrate PipelineIQ into Slack, dashboards, or CI/CD pipelines:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Analyze a log
```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "log_text": "npm error ERESOLVE unable to resolve...",
    "pipeline_type": "github-actions"
  }'
```

### Streaming analysis
```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"log_text": "...", "pipeline_type": "github-actions", "stream": true}'
```

### Follow-up chat
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I prevent this?", "log_context": "..."}'
```

### Teach a resolution
```bash
curl -X POST http://localhost:8000/teach \
  -H "Content-Type: application/json" \
  -d '{
    "log_text": "...",
    "pipeline_type": "github-actions",
    "root_cause": "React 18 peer dep conflict",
    "error_type": "DependencyConflict",
    "fix_applied": "Added --legacy-peer-deps",
    "fix_commands": ["npm ci --legacy-peer-deps"],
    "tags": ["npm", "peer-deps"]
  }'
```

---

## GitHub Actions Auto-Analysis

Add this to your workflow to auto-analyze failures:

```yaml
analyze-on-failure:
  needs: [your-build-job]
  if: failure()
  runs-on: ubuntu-latest
  steps:
    - name: Analyze failure with PipelineIQ
      run: |
        LOG=$(gh run view ${{ github.run_id }} --log-failed 2>&1 | head -500)
        curl -s -X POST ${{ secrets.PIPELINEIQ_URL }}/analyze \
          -H "Content-Type: application/json" \
          -d "{\"log_text\": $(echo $LOG | jq -Rs .), \"pipeline_type\": \"github-actions\"}" \
          | jq '.fix.summary'
      env:
        GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## Project Structure

```
pipelineiq/
├── cli.py           ← Interactive terminal GPT (main entry point)
├── analyzer.py      ← Orchestrator: RAG + LLM wired together
├── rag_engine.py    ← Chunker + Embedder + ChromaDB vector store
├── llm_client.py    ← Ollama client (stream, chat, JSON mode)
├── server.py        ← Optional FastAPI REST server
├── requirements.txt
└── chroma_db/       ← Auto-created: persistent vector store
```

---

## Supported Pipeline Types

| Type | Flag |
|---|---|
| GitHub Actions | `github-actions` |
| Jenkins | `jenkins` |
| GitLab CI | `gitlab` |
| Terraform | `terraform` |
| Docker Build | `docker` |
| Any other log | `custom` |

## Supported Ollama Models

| Model | Best For |
|---|---|
| `llama3` | Best overall, recommended |
| `mistral` | Faster responses |
| `codellama` | Code-heavy errors |
| `deepseek-coder` | Alternative code model |
| `phi3` | Very fast, lower RAM |
