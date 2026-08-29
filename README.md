# mcp-doctor

[![CI](https://github.com/vishalhabib99/mcp-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/vishalhabib99/mcp-doctor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A static analysis CLI that audits **MCP (Model Context Protocol) server** implementations for the things that actually break an agent calling them: missing tool descriptions, undocumented parameters, no error handling, no README coverage.

The MCP ecosystem is growing faster than the conventions around building a *good* server have settled. Most servers are hand-written in an afternoon and never checked against anything. `mcp-doctor` is a linter for that gap — point it at a repo, get a score and a concrete list of what to fix.

```
$ mcp-doctor examples/bad_server
mcp-doctor report
Score: 13%  Grade: F  (2 tool(s) found)

  [FAIL] do_thing (server.py:9)
      ERROR  Tool has no description. An agent cannot decide when to call this.
      WARNING  2/2 parameters have no type annotation.
      WARNING  Parameters aren't documented in an Args: section — the model only sees names, not intent.
      WARNING  No try/except — an exception here will raise a raw traceback back through the MCP transport.
  [FAIL] run (server.py:15)
      ERROR  Tool has no description. An agent cannot decide when to call this.
      WARNING  1/1 parameters have no type annotation.
      WARNING  Parameters aren't documented in an Args: section — the model only sees names, not intent.
      ERROR  Bare 'except:' swallows all errors including cancellation — catch specific exceptions.

Repo-level
  ERROR  No README found.
  WARNING  No LICENSE file — undermines adoption.
  WARNING  No test files found.
  WARNING  No pyproject.toml/requirements.txt/setup.py — dependencies aren't pinned.
```

```
$ mcp-doctor examples/good_server
mcp-doctor report
Score: 100%  Grade: A  (1 tool(s) found)

  [OK] get_forecast (server.py:9)
```

## Install

Not on PyPI yet — install straight from the repo:

```bash
pip install git+https://github.com/vishalhabib99/mcp-doctor.git
```

or clone it and install locally:

```bash
git clone https://github.com/vishalhabib99/mcp-doctor.git
cd mcp-doctor
pip install -e .
```

## Usage

```bash
mcp-doctor .                      # audit the current directory
mcp-doctor path/to/server         # audit a specific path
mcp-doctor . --json               # machine-readable output
mcp-doctor . --fail-under 80      # exit 1 if score drops below 80% — wire into CI
```

## What it checks

**Per tool** (detects both the FastMCP `@mcp.tool()` decorator style and the low-level SDK's `Tool(name=..., description=..., inputSchema=...)` style):

| Check | Why it matters |
|---|---|
| Has a description | An agent picks tools by reading descriptions. No description, no calls. |
| Description isn't trivially short | A 3-character description is functionally the same as none. |
| Parameters are type-annotated | Untyped params usually mean the schema exposed to the model is untyped too. |
| Parameters are documented (`Args:` section, or schema `description` fields) | The model sees parameter names but not intent unless you spell it out. |
| Has error handling | An unhandled exception in a tool call surfaces as a raw traceback through the MCP transport instead of a usable error message. |
| No bare `except:` | Swallows everything, including cancellation — a real production bug pattern, not just a style nit. |

**Repo-level:**

- README exists, and mentions every tool you export
- LICENSE exists
- Tests exist
- Dependencies are declared (`pyproject.toml` / `requirements.txt` / `setup.py`)
- No hardcoded-looking API keys/secrets/tokens in source

## Real-world spot check

Run against three servers from the official [`modelcontextprotocol/servers`](https://github.com/modelcontextprotocol/servers) repo:

- **`src/fetch`** — **100% / A**. Clean.
- **`src/git`**, **`src/time`** — flagged as **parse errors**, not false passes. Both use Python `match` statements (3.10+ syntax); `mcp-doctor`'s AST parser follows the grammar of whatever Python interpreter runs it, so under Python 3.9 those files can't be parsed. Rather than silently skip them and report a misleadingly clean score, `mcp-doctor` surfaces this as an explicit error: *"N file(s) could not be parsed and were skipped."* Run it under Python ≥3.10 to analyze those files correctly.

## Known limitations

- **Python only.** No TypeScript/JS support yet, despite that being a large share of the MCP server ecosystem — see [Roadmap](#roadmap).
- **AST-based, single-pass.** Tools constructed dynamically in a loop, or schemas built from something other than a dict literal or a `pydantic` `model_json_schema()` call, won't be fully introspected — you'll get the tool detected but a blind spot on its parameter-level checks rather than a false failure.
- **Parses with the running interpreter's grammar.** See the spot check above — run under a Python version that matches or exceeds the syntax used in the server you're auditing.

## Roadmap

- [ ] TypeScript/JS server support (the official SDK's dominant language)
- [ ] Publish to PyPI
- [ ] `--fix` for the mechanical stuff (stub `Args:` sections, wrap in try/except)
- [ ] GitHub Action for one-line CI integration

## Contributing

Issues and PRs welcome. The test suite (`pytest`) covers the analyzer directly and the CLI end-to-end against the fixtures in `examples/` — add a fixture case for anything you fix.

## License

MIT — see [LICENSE](LICENSE).
