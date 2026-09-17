"""Tests for the live registration gate — pure data-in/data-out, no
subprocess or connection needed, since check_tool_registration never makes
one. Fixtures are shaped like real `tools/list` JSON (dicts with camelCase
keys) plus a small stub object to verify the SDK-object duck-typing path
too, not just dicts."""

from mcp_doctor.gate import check_tool_registration


def test_well_documented_tool_is_not_flagged():
    result = check_tool_registration(
        "get_weather", "Fetches the current weather for a named city.",
        {"readOnlyHint": True},
    )
    assert result.flagged is False


def test_missing_description_is_flagged_as_error():
    result = check_tool_registration("get_weather", "", None)
    assert result.flagged is True
    assert result.issues[0].check == "description"
    assert result.issues[0].severity == "error"


def test_none_description_is_treated_as_missing():
    result = check_tool_registration("get_weather", None, None)
    assert result.flagged is True
    assert result.issues[0].severity == "error"


def test_whitespace_only_description_is_treated_as_missing():
    result = check_tool_registration("get_weather", "   \n  ", None)
    assert result.flagged is True
    assert result.issues[0].severity == "error"


def test_vague_short_description_is_flagged_as_warning():
    result = check_tool_registration("get_weather", "weather", None)
    assert result.flagged is True
    assert result.issues[0].check == "description"
    assert result.issues[0].severity == "warning"
    assert "7 chars" in result.issues[0].message


def test_annotation_contradiction_is_caught_from_a_dict():
    result = check_tool_registration(
        "delete_file", "Deletes a file by path.",
        {"readOnlyHint": True, "destructiveHint": True},
    )
    assert result.flagged is True
    assert any(i.check == "annotation_contradiction" for i in result.issues)
    issue = next(i for i in result.issues if i.check == "annotation_contradiction")
    assert issue.category == "security"


def test_explicit_destructive_false_alongside_read_only_is_not_flagged():
    # Real finding from dogfooding against the official
    # @modelcontextprotocol/server-memory reference server, before this
    # ever shipped: that server explicitly declares all four
    # ToolAnnotations fields on every tool as a matter of good, complete
    # practice (read_graph: readOnlyHint=true, destructiveHint=false) —
    # sensible and consistent, not a bug. A presence-only rule (the static
    # analyzer's source-level rule, ported unchanged) flagged this as
    # contradictory; fixed to require destructiveHint's actual *value* to
    # be true, not merely present, before this test was written to lock
    # the corrected behavior in.
    result = check_tool_registration(
        "read_graph", "Reads the entire knowledge graph.",
        {"readOnlyHint": True, "destructiveHint": False},
    )
    assert result.flagged is False


def test_read_only_alone_is_not_flagged():
    result = check_tool_registration(
        "list_files", "Lists files in a directory.",
        {"readOnlyHint": True},
    )
    assert result.flagged is False


def test_destructive_alone_is_not_flagged():
    result = check_tool_registration(
        "delete_file", "Deletes a file by path.",
        {"destructiveHint": True},
    )
    assert result.flagged is False


def test_no_annotations_at_all_only_checks_description():
    result = check_tool_registration("list_files", "Lists files in a directory.", None)
    assert result.flagged is False


class _StubAnnotations:
    """Mimics an SDK object (e.g. mcp.types.ToolAnnotations) without
    depending on the mcp package: snake_case attributes, duck-typed."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_sdk_style_object_contradiction_is_caught():
    annotations = _StubAnnotations(read_only_hint=True, destructive_hint=True)
    result = check_tool_registration("delete_file", "Deletes a file by path.", annotations)
    assert any(i.check == "annotation_contradiction" for i in result.issues)


def test_sdk_style_object_with_destructive_false_is_clean():
    annotations = _StubAnnotations(read_only_hint=True, destructive_hint=False)
    result = check_tool_registration("read_graph", "Reads the entire knowledge graph.", annotations)
    assert result.flagged is False


def test_sdk_style_object_without_destructive_hint_set_is_clean():
    annotations = _StubAnnotations(read_only_hint=True)
    result = check_tool_registration("list_files", "Lists files in a directory.", annotations)
    assert result.flagged is False
