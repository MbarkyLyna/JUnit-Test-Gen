# Spring Test Generator

## Overview

A web based system that generates JUnit tests for Spring Boot applications using a locally hosted, open source LLM. Instead of reasoning over interfaces alone, it resolves the actual Spring wired implementation behind each dependency so generated tests reflect real runtime behavior. The system targets raising test coverage to at least 80 percent by identifying coverage gaps and generating tests aimed at closing them.

## Tech Stack

- Frontend: React (Vite)
- Backend: Python, FastAPI
- LLM: Ollama, running `qwen2.5-coder:1.5b` locally by default (override with `OLLAMA_MODEL` env var), no external API cost
- Coverage: JaCoCo
- Test execution: Maven, run inside a sandboxed Docker container

## How It Works

1. User imports a Spring Boot project via **ZIP upload** or **GitHub clone**. The backend extracts/clones into an isolated workspace and shows a file tree plus static counts (classes, packages, tests).
2. On analysis, the backend injects the JaCoCo plugin if missing, runs the test suite inside a Docker sandbox, and returns per class/package coverage.
3. User picks a scope: a single class, multiple classes (checkbox multi-select), or the whole project.
4. For each target class, the backend statically resolves the real implementation behind each dependency (or marks it an external framework bean, e.g. `RestTemplate`, to be mocked), and filters out trivial code (Lombok, getters/setters, `equals`/`hashCode`/`toString`) from the coverage gaps.
5. The backend prompts the local LLM with the class code, resolved dependency context, and uncovered lines, then parses the generated test from the raw response.
6. Generated tests are written to the test directory and re-run in the Docker sandbox to confirm they compile and pass. Updated coverage and a per class status (full coverage, partial, failed to compile, no change) are returned.

Multi-class and full-project generation run **sequentially** (one class at a time, LLM → Docker verification → cooldown) with progress tracking and cancel support.

## Sandboxing

All Maven execution runs inside a minimal, non root Docker container with fixed memory and CPU limits.

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `qwen2.5-coder:1.5b` | Ollama model for test generation |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API base URL |
| `MIN_FREE_RAM_GB` | `2.5` | Minimum free host RAM (GB) required before multi-class or project generation |
| `GENERATION_COOLDOWN_SECONDS` | `3` | Pause between classes during batch generation |
| `GIT_CLONE_TIMEOUT_SECONDS` | `120` | Timeout for `git clone` operations |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a project ZIP |
| `POST` | `/api/clone` | Clone a public GitHub repo (`repo_url`, optional `branch`) |
| `POST` | `/api/analyze/{session_id}` | Run JaCoCo analysis in Docker |
| `POST` | `/api/generate/{session_id}` | Generate for a single class (`scope: "class"`) |
| `POST` | `/api/generate/{session_id}/start` | Start async batch job (`scope: "classes"` or `"project"`) |
| `GET` | `/api/generate/jobs/{job_id}` | Poll job progress |
| `POST` | `/api/generate/jobs/{job_id}/abort` | Cancel a running batch job |
| `GET` | `/api/health` | Ollama availability, model name, RAM status |

## Requirements

Docker, Ollama (with `qwen2.5-coder:1.5b` pulled), Node.js, Python 3, git (for GitHub clone)

## Running

1. `ollama pull qwen2.5-coder:1.5b`
2. `docker build -t spring-test-gen-sandbox:latest .`
3. Backend: `cd backend && uvicorn app.main:app --reload --port 8000`
4. Frontend: `cd frontend && npm run dev`
5. Import a project (ZIP or GitHub URL), run analysis, then generate tests for a class, selected classes, or the whole project.

### Smoke tests

```bash
# Standalone LLM output check (no Docker)
cd backend && python scripts/test_model_standalone.py

# Staged API verification (clone → analyze → single → multi-select guard)
cd backend && python scripts/verify_pipeline.py
```
