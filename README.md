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

## GitHub Action

Gate PRs on server quality without installing anything yourself:

```yaml
- uses: vishalhabib99/mcp-doctor@v1
  with:
    path: .              # default: repo root
    fail-under: 70        # default: 0 (report only, don't fail the build)
    comment: true          # default: true — posts/updates a PR comment with the report
```

The report also gets written to the job summary either way. `@v1` tracks the latest `v1.x` release; pin an exact tag or commit SHA instead if you need stricter reproducibility.

## What it checks

Audits both Python and TypeScript/JavaScript servers in the same repo. Python detects the FastMCP `@mcp.tool()` decorator style and the low-level SDK's `Tool(name=..., description=..., inputSchema=...)` style; TS/JS detects the official SDK's `server.registerTool(name, config, handler)` and `server.tool(name, description, schema, handler)` styles, including the common pattern where the config object or Zod schema is a same-file `const` reference rather than inline. The same checks apply either way — a description, per-parameter docs (`Args:`/`Field(description=...)` in Python, `.describe(...)` on each Zod field in TS), and a try/except (or try/catch).

**Per tool**:

| Check | Why it matters |
|---|---|
| Has a description | An agent picks tools by reading descriptions. No description, no calls. |
| Description isn't trivially short | A 3-character description is functionally the same as none. |
| Parameters are type-annotated | Untyped params usually mean the schema exposed to the model is untyped too. |
| Parameters are documented (`Args:` section, or schema `description` fields) | The model sees parameter names but not intent unless you spell it out. |
| Has error handling | FastMCP catches an unhandled exception and returns a structured error either way — this check is about message quality, not transport safety: a tool-level catch can raise a specific, actionable message instead of leaving the model with generic exception text. |
| No bare `except:` | Swallows everything, including cancellation — a real production bug pattern, not just a style nit. |

**Repo-level:**

- README exists, and mentions every tool you export
- LICENSE exists
- Tests exist
- Dependencies are declared (`pyproject.toml` / `requirements.txt` / `setup.py` / `package.json`)
- No hardcoded-looking API keys/secrets/tokens in source
- Tool names conform to the [spec's Tool Names guidance](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#tool-names) (1–128 chars, `A-Z a-z 0-9 _ - .` only, unique within the server)

## Real-world spot check

Run against three servers from the official [`modelcontextprotocol/servers`](https://github.com/modelcontextprotocol/servers) repo:

- **`src/fetch`** — **100% / A**. Clean.
- **`src/git`**, **`src/time`** — flagged as **parse errors**, not false passes. Both use Python `match` statements (3.10+ syntax); `mcp-doctor`'s AST parser follows the grammar of whatever Python interpreter runs it, so under Python 3.9 those files can't be parsed. Rather than silently skip them and report a misleadingly clean score, `mcp-doctor` surfaces this as an explicit error: *"N file(s) could not be parsed and were skipped."* Run it under Python ≥3.10 to analyze those files correctly.

Later spot-checked against 4 more real, in-the-wild servers (awslabs' `aws-documentation-mcp-server`, `mcp-google-ads`, `sv-excel-agent`, and Home Assistant's `ha-mcp`, an 88-tool server). That run caught two real precision bugs: the secret scanner was flagging test fixtures and identifier-style constant names (`SERVICE_GET_CALLER_TOKEN = "get_caller_token"`) as hardcoded credentials, and the param-docs check didn't recognize `Annotated[T, Field(description=...)]` — a completely valid, schema-level way to document a parameter — as documentation at all, since it only looked for a docstring `Args:` section. Both fixed.

A maintainer on `ha-mcp` reviewed the resulting report in detail and pushed back further, correctly: the param-docs check still missed descriptions reached through a shared, cross-file type alias (`Annotated[..., Field(description=...)]` assigned to a name and imported elsewhere) and prose under non-`Args:` headings (e.g. `**Parameters:**`, including bulleted `- param: ...` lines), and — more importantly — the error-handling check's own message was wrong. It claimed a missing try/except lets a raw traceback leak through the MCP transport; FastMCP's `call_tool` dispatcher actually wraps every call and converts any exception into a structured error regardless, which the pushback prompted me to verify directly against FastMCP's source. Both the alias/heading gaps and the error-handling message are now fixed — see [homeassistant-ai/ha-mcp#2324](https://github.com/homeassistant-ai/ha-mcp/issues/2324) for the full exchange.

The maintainer offered to leave a follow-up issue open if it were grounded in the actual spec and FastMCP's own guidelines rather than another pass of the same heuristics. Read the [current spec's Tools page](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) end to end looking for exactly that: one concrete, checkable gap emerged — the normative **Tool Names** section (length, character set, uniqueness), which mcp-doctor didn't check at all — now added. Checked it against ha-mcp's real 88 tool names before claiming anything: all of them already comply, so this doesn't reopen anything there — it's a real gap closed for the next server that isn't as careful, not a finding to hand back.

## Known limitations

- **AST-based, single-pass.** Tools constructed dynamically in a loop, or schemas built from something other than a dict literal or a `pydantic` `model_json_schema()` call, won't be fully introspected — you'll get the tool detected but a blind spot on its parameter-level checks rather than a false failure. A dynamic tool *name* (not a string literal, e.g. built in a loop) means the tool is skipped entirely rather than misattributed.
- **Parses with the running interpreter's grammar (Python side).** See the spot check above — run under a Python version that matches or exceeds the syntax used in the server you're auditing.
- **Doesn't follow delegation.** If a tool function immediately hands off to a helper that has its own try/except (or try/catch), the error-handling check only looks at the decorated function's own body and reports a false positive — it has no call-graph analysis.
- **TS/JS const resolution is same-file only.** Unlike the Python side's cross-file `Field` type-alias resolution, a TS config object or Zod schema referenced via an import from another file won't be resolved — only same-file `const` references.

## Roadmap

- [x] TypeScript/JS server support (the official SDK's dominant language) — `registerTool`/`tool` styles, same-file const resolution
- [ ] Publish to PyPI
- [ ] `--fix` for the mechanical stuff (stub `Args:` sections, wrap in try/except)
- [ ] GitHub Action for one-line CI integration

## Contributing

Issues and PRs welcome. The test suite (`pytest`) covers the analyzer directly and the CLI end-to-end against the fixtures in `examples/` — add a fixture case for anything you fix.

## License

MIT — see [LICENSE](LICENSE).
