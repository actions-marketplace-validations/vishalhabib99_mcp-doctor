"""Static analysis of MCP (Model Context Protocol) server implementations.

Walks a Python codebase, finds tool definitions authored with either the
FastMCP decorator style (``@mcp.tool()``) or the low-level SDK style
(``Tool(name=..., description=..., inputSchema=...)``), and scores them
against a set of conformance and quality checks that matter for an agent
actually calling the tool at runtime: does it have a description an LLM
can act on, are parameters documented and typed, does it handle errors
instead of leaking stack traces back to the model, is it documented for
humans in the README.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SECRET_PATTERN = re.compile(
    r"""(api[_-]?key|secret|token|password|access[_-]?key)\s*=\s*["'](?=[^"']*\d)[A-Za-z0-9_\-/+]{12,}["']""",
    re.IGNORECASE,
)

FASTMCP_DECORATOR_NAMES = {"tool"}


@dataclass
class ToolIssue:
    tool: str
    file: str
    line: int
    check: str
    message: str
    severity: str  # "error" | "warning"


@dataclass
class ToolFinding:
    name: str
    file: str
    line: int
    has_description: bool
    description_len: int
    param_count: int
    typed_param_count: int
    has_docstring_params: bool
    has_try_except: bool
    has_bare_except: bool
    issues: list[ToolIssue] = field(default_factory=list)


@dataclass
class RepoIssue:
    check: str
    message: str
    severity: str


@dataclass
class Report:
    tools: list[ToolFinding]
    repo_issues: list[RepoIssue]
    score: int
    max_score: int

    @property
    def grade(self) -> str:
        if self.max_score == 0:
            return "N/A"
        pct = self.score / self.max_score * 100
        if pct >= 90:
            return "A"
        if pct >= 80:
            return "B"
        if pct >= 70:
            return "C"
        if pct >= 60:
            return "D"
        return "F"

    @property
    def percent(self) -> int:
        if self.max_score == 0:
            return 0
        return round(self.score / self.max_score * 100)


def _get_docstring_sections(docstring: str | None) -> set[str]:
    if not docstring:
        return set()
    params = set()
    in_args = False
    for line in docstring.splitlines():
        stripped = line.strip()
        if re.match(r"^(Args|Arguments|Params|Parameters):\s*$", stripped):
            in_args = True
            continue
        if in_args:
            if not stripped or re.match(r"^(Returns|Raises|Yields|Examples?):\s*$", stripped):
                in_args = False
                continue
            m = re.match(r"^\**([A-Za-z_][A-Za-z0-9_]*)\**\s*(\(.*\))?\s*:", stripped)
            if m:
                params.add(m.group(1))
    return params


def _field_call_has_description(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
    if name != "Field":
        return False
    desc = _kwarg_str(node, "description")
    return bool(desc and desc.strip())


def _param_documented_via_field(arg: ast.arg, default: ast.expr | None) -> bool:
    """Pydantic-style per-parameter docs: Annotated[T, Field(description=...)] or `x: T = Field(description=...)`."""
    annotation = arg.annotation
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        base_name = base.attr if isinstance(base, ast.Attribute) else (base.id if isinstance(base, ast.Name) else None)
        if base_name == "Annotated":
            sl = annotation.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            if any(_field_call_has_description(e) for e in elts):
                return True
    return _field_call_has_description(default) if default is not None else False


def _find_decorator_call(dec: ast.expr, names: set[str]) -> ast.Call | None:
    node = dec
    if isinstance(node, ast.Call):
        func = node.func
    else:
        func = node
    attr_name = None
    if isinstance(func, ast.Attribute):
        attr_name = func.attr
    elif isinstance(func, ast.Name):
        attr_name = func.id
    if attr_name in names:
        return node if isinstance(node, ast.Call) else None
    return None


def _kwarg_str(call: ast.Call, key: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _contains_try_except(node: ast.AST) -> tuple[bool, bool]:
    has_try = False
    has_bare = False
    for n in ast.walk(node):
        if isinstance(n, ast.Try):
            has_try = True
            for handler in n.handlers:
                if handler.type is None:
                    has_bare = True
    return has_try, has_bare


def _analyze_function_as_tool(fn: ast.FunctionDef | ast.AsyncFunctionDef, file: str, description_override: str | None = None) -> ToolFinding:
    docstring = ast.get_docstring(fn)
    description = description_override or (docstring.splitlines()[0].strip() if docstring else "")
    all_args = fn.args.args
    args = [a for a in all_args if a.arg not in ("self", "cls")]
    typed = sum(1 for a in args if a.annotation is not None)
    doc_params = _get_docstring_sections(docstring)

    defaults_by_arg = dict(zip(all_args[len(all_args) - len(fn.args.defaults):], fn.args.defaults))
    field_documented_names = {a.arg for a in args if _param_documented_via_field(a, defaults_by_arg.get(a))}
    documented_count = len(doc_params | field_documented_names)

    has_try, has_bare = _contains_try_except(fn)

    finding = ToolFinding(
        name=fn.name,
        file=file,
        line=fn.lineno,
        has_description=bool(description.strip()),
        description_len=len(description.strip()),
        param_count=len(args),
        typed_param_count=typed,
        has_docstring_params=documented_count >= len(args) and len(args) > 0,
        has_try_except=has_try,
        has_bare_except=has_bare,
    )

    if not finding.has_description:
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "description",
            "Tool has no description. An agent cannot decide when to call this.",
            "error",
        ))
    elif finding.description_len < 10:
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "description",
            f"Description is only {finding.description_len} chars — likely just restates the name.",
            "warning",
        ))

    if args and typed < len(args):
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "types",
            f"{len(args) - typed}/{len(args)} parameters have no type annotation.",
            "warning",
        ))

    if args and not finding.has_docstring_params:
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "param_docs",
            "Parameters aren't documented — no Args: docstring section and no per-parameter "
            "Field(description=...) — the model only sees names, not intent.",
            "warning",
        ))

    if not has_try:
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "error_handling",
            "No try/except — an exception here will raise a raw traceback back through the MCP transport.",
            "warning",
        ))
    if has_bare:
        finding.issues.append(ToolIssue(
            fn.name, file, fn.lineno, "bare_except",
            "Bare 'except:' swallows all errors including cancellation — catch specific exceptions.",
            "error",
        ))

    return finding


def _find_fastmcp_tools(tree: ast.Module, file: str) -> list[ToolFinding]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = _find_decorator_call(dec, FASTMCP_DECORATOR_NAMES)
            if call is None:
                # bare @mcp.tool with no parens still counts
                attr = dec.attr if isinstance(dec, ast.Attribute) else (dec.id if isinstance(dec, ast.Name) else None)
                if attr not in FASTMCP_DECORATOR_NAMES:
                    continue
                findings.append(_analyze_function_as_tool(node, file))
                break
            description_override = _kwarg_str(call, "description")
            findings.append(_analyze_function_as_tool(node, file, description_override))
            break
    return findings


def _find_lowlevel_tools(tree: ast.Module, file: str) -> list[ToolFinding]:
    """Find Tool(name=..., description=..., inputSchema=...) constructor calls."""
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name_id = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
        if name_id != "Tool":
            continue
        name = _kwarg_str(node, "name") or "<unnamed>"
        description = _kwarg_str(node, "description") or ""
        schema_kw = next((kw for kw in node.keywords if kw.arg == "inputSchema"), None)
        param_count = 0
        typed_param_count = 0
        has_docstring_params = True
        if schema_kw is not None and isinstance(schema_kw.value, ast.Dict):
            for k, v in zip(schema_kw.value.keys, schema_kw.value.values):
                if isinstance(k, ast.Constant) and k.value == "properties" and isinstance(v, ast.Dict):
                    param_count = len(v.keys)
                    for pk, pv in zip(v.keys, v.values):
                        if isinstance(pv, ast.Dict):
                            has_desc = any(
                                isinstance(pk2, ast.Constant) and pk2.value == "description"
                                for pk2 in pv.keys
                            )
                            has_type = any(
                                isinstance(pk2, ast.Constant) and pk2.value == "type"
                                for pk2 in pv.keys
                            )
                            if has_desc:
                                typed_param_count += 1
                            if not has_type:
                                has_docstring_params = False

        finding = ToolFinding(
            name=name,
            file=file,
            line=node.lineno,
            has_description=bool(description.strip()),
            description_len=len(description.strip()),
            param_count=param_count,
            typed_param_count=typed_param_count,
            has_docstring_params=typed_param_count >= param_count and param_count > 0,
            has_try_except=True,  # not attributable to a single function body here
            has_bare_except=False,
        )
        if not finding.has_description:
            finding.issues.append(ToolIssue(
                name, file, node.lineno, "description",
                "Tool has no description. An agent cannot decide when to call this.",
                "error",
            ))
        elif finding.description_len < 10:
            finding.issues.append(ToolIssue(
                name, file, node.lineno, "description",
                f"Description is only {finding.description_len} chars — likely just restates the name.",
                "warning",
            ))
        if param_count and typed_param_count < param_count:
            finding.issues.append(ToolIssue(
                name, file, node.lineno, "param_docs",
                f"{param_count - typed_param_count}/{param_count} input schema properties have no description.",
                "warning",
            ))
        findings.append(finding)
    return findings


def _is_test_file(path: Path) -> bool:
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(part in ("test", "tests") for part in path.parts)


def _scan_secrets(py_files: list[Path]) -> list[RepoIssue]:
    issues = []
    for f in py_files:
        if _is_test_file(f):
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if SECRET_PATTERN.search(line):
                issues.append(RepoIssue(
                    "secrets",
                    f"{f.name}:{i} looks like a hardcoded credential.",
                    "error",
                ))
    return issues


def analyze_repo(root: Path) -> Report:
    py_files = [p for p in root.rglob("*.py") if "/.git/" not in str(p) and "/venv/" not in str(p) and "/node_modules/" not in str(p)]

    tools: list[ToolFinding] = []
    unparseable: list[str] = []
    for f in py_files:
        if _is_test_file(f):
            continue
        try:
            tree = ast.parse(f.read_text(errors="ignore"), filename=str(f))
        except SyntaxError:
            unparseable.append(str(f.relative_to(root)))
            continue
        rel = str(f.relative_to(root))
        tools.extend(_find_fastmcp_tools(tree, rel))
        tools.extend(_find_lowlevel_tools(tree, rel))

    repo_issues: list[RepoIssue] = []

    if unparseable:
        repo_issues.append(RepoIssue(
            "parse_error",
            f"{len(unparseable)} file(s) could not be parsed and were skipped — results below may be "
            f"incomplete. This usually means the file uses syntax newer than the Python running "
            f"mcp-doctor (e.g. `match` statements need Python >=3.10). Skipped: "
            + ", ".join(unparseable[:5]) + ("…" if len(unparseable) > 5 else ""),
            "error",
        ))

    readme = next((p for p in root.glob("README*")), None)
    readme_text = readme.read_text(errors="ignore") if readme else ""
    if not readme:
        repo_issues.append(RepoIssue("readme", "No README found.", "error"))
    else:
        undocumented = [t.name for t in tools if t.name not in readme_text]
        if undocumented:
            repo_issues.append(RepoIssue(
                "readme",
                f"{len(undocumented)} tool(s) not mentioned in README: {', '.join(undocumented[:5])}"
                + ("…" if len(undocumented) > 5 else ""),
                "warning",
            ))

    if not any(root.glob("LICENSE*")):
        repo_issues.append(RepoIssue("license", "No LICENSE file — undermines adoption.", "warning"))

    has_tests = any(root.rglob("test_*.py")) or any(root.rglob("*_test.py")) or (root / "tests").is_dir()
    if not has_tests:
        repo_issues.append(RepoIssue("tests", "No test files found.", "warning"))

    if not (root / "pyproject.toml").exists() and not (root / "requirements.txt").exists() and not (root / "setup.py").exists():
        repo_issues.append(RepoIssue("packaging", "No pyproject.toml/requirements.txt/setup.py — dependencies aren't pinned.", "warning"))

    repo_issues.extend(_scan_secrets(py_files))

    score = 0
    max_score = 0

    for t in tools:
        max_score += 10
        score += 10
        for issue in t.issues:
            score -= 3 if issue.severity == "error" else 1

    max_score += 10  # readme presence
    if readme:
        score += 10
    max_score += 5  # license
    if not any(i.check == "license" for i in repo_issues):
        score += 5
    max_score += 5  # tests
    if has_tests:
        score += 5
    max_score += 5  # packaging
    if not any(i.check == "packaging" for i in repo_issues):
        score += 5
    for i in repo_issues:
        if i.check in ("secrets", "parse_error"):
            score -= 5

    score = max(0, score)
    max_score = max(max_score, 1)

    return Report(tools=tools, repo_issues=repo_issues, score=score, max_score=max_score)
