# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FireRed-OpenStoryline is an AI-powered video creation platform that transforms natural language descriptions into complete videos through a conversational interface. It combines a LangChain agent, MCP (Model Context Protocol) server, and modular video processing nodes.

**Python >= 3.11 required. Apache 2.0 license.**

## Commands

### Setup
```bash
conda create -n storyline python=3.11 && conda activate storyline
sh build_env.sh                    # auto-install (Linux/macOS)
# or manual:
chmod +x download.sh && ./download.sh
pip install -r requirements.txt
```

### Running
```bash
# Start MCP server (required first, separate terminal)
PYTHONPATH=src python -m open_storyline.mcp.server

# Web UI
uvicorn agent_fastapi:app --host 127.0.0.1 --port 8005

# CLI
python cli.py

# Both together (production)
./run.sh   # MCP on port 8001, Web on port 7860
```

### Docker
```bash
docker pull openstoryline/openstoryline:v1.0.1
docker run -v $(pwd)/config.toml:/app/config.toml \
           -v $(pwd)/outputs:/app/outputs \
           -p 7860:7860 openstoryline/openstoryline:v1.0.1
```

### Config update script
```bash
python scripts/update_config.py config.toml llm.model gpt-4
```

## Architecture

### Three entry points
1. **`agent_fastapi.py`** - FastAPI web server with WebSocket streaming, file uploads, session management
2. **`cli.py`** - Interactive CLI with message history
3. **`src/open_storyline/mcp/server.py`** - FastMCP server exposing nodes as tools (streamable-http transport, port 8001)

### Agent layer (`src/open_storyline/agent.py`)
- `build_agent()` constructs a LangChain agent with LLM, tools from MCP, and middleware
- `ClientContext` holds runtime state: session ID, config, model pools
- Tools are called in a loop; interceptors inject/extract media and config before/after each call

### Node system (`src/open_storyline/nodes/`)
Each video processing capability is a **node** - a class extending `BaseNode` with:
- `NodeMeta` (name, description, node_id, node_kind)
- Input/output Pydantic schemas defined in `node_schema.py`
- `@NODE_REGISTRY.register` decorator for auto-discovery
- Async `__call__(self, state, **kwargs)` method

Nodes are registered as MCP tools via `src/open_storyline/mcp/register_tools.py`. The list of active nodes is configured in `config.toml` under `[local_mcp_server].available_nodes`.

**Core nodes** (`src/open_storyline/nodes/core_nodes/`): LoadMedia, SearchMedia, SplitShots, LocalASR, SpeechRoughCut, UnderstandClips, FilterClips, GroupClips, GenerateScript, ScriptTemplateRecommendation, GenerateVoiceover, SelectBGM, RecommendTransition, RecommendText, PlanTimelinePro, RenderVideo.

### Middleware (`src/open_storyline/mcp/hooks/`)
- **`node_interceptors.py`** - Before/after hooks: inject media content, save results, inject TTS config
- **`chat_middleware.py`** - Log tool requests, handle errors, stream progress updates

### Storage (`src/open_storyline/storage/`)
- `agent_memory.py` - Artifact store for workflow results, organized by session
- `session_manager.py` - Session lifecycle, expiration, cache cleanup

### Configuration (`config.toml`)
Hierarchical TOML config parsed by Pydantic (`src/open_storyline/config.py`). Key sections:
- `[llm]` / `[vlm]` - LLM and Vision model API credentials (must be filled before use)
- `[local_mcp_server]` - Transport, port, available nodes
- `[project]` - Media/output directory paths
- `[search_media]` - Pexels API key
- `[developer]` - `developer_mode = true` enables verbose debugging

Environment variable overrides: `OPENSTORYLINE_CONFIG`, `OPENSTORYLINE_LLM_*`, `OPENSTORYLINE_VLM_*`.

### Prompt templates
LLM prompts live in `prompts/` and `prompts/tasks/`, loaded by `src/open_storyline/utils/prompts.py`.

### Web UI
Static HTML/CSS/JS in `web/` served by FastAPI. No build step required.

## Adding a New Node

1. Create `src/open_storyline/nodes/core_nodes/my_node.py`
2. Extend `BaseNode`, set `meta = NodeMeta(...)`, implement async `__call__`
3. Use `@NODE_REGISTRY.register` decorator
4. Define input/output schemas in `src/open_storyline/nodes/node_schema.py`
5. Add the class name to `available_nodes` list in `config.toml`

## PYTHONPATH

All imports under `src/` require `PYTHONPATH=src` (e.g., `from open_storyline.agent import ...`). The `run.sh` script sets this automatically. When running manually, prefix commands with `PYTHONPATH=src`.

## Claude Code Skills

Built-in skills in `.claude/skills/`:
- `/openstoryline-install` - Install, configure, and verify the setup
- `/openstoryline-use` - Start services and run video editing workflows
