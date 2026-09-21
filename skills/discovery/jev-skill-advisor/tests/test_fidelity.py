import pytest

from jev_skill_advisor.fidelity import FidelityError, compare_markdown


NOTICE = "The following metadata is part of the canonical skill contract and remains authoritative for this pilot."


EXPECTED = f'''# Sample Skill

Do **not** change [the link](https://example.com). Try *"this exact thing"*.

| Task | Command |
|---|---|
| Read | `tool get <id>` |
| Write | Never |

- First
  continuation
  - Nested

```text
if allowed:
    run("exact")
```

## Preserved source metadata

{NOTICE}

```yaml
version: 1.0.0
guardrails:
  - never delete
  - preserve links
```
'''


NORMALIZED = f'''Do not change [the link](https://example.com). Try \\*"this exact thing"\\*.
<table header-row="true">
<tr><td>Task</td><td>Command</td></tr>
<tr><td>Read</td><td>`tool get <id>`</td></tr>
<tr><td>Write</td><td>Never</td></tr>
</table>
- First
\tcontinuation
\t- Nested
```plain text
if allowed:
    run("exact")
```
## Preserved source metadata
{NOTICE}
```yaml
version: 1.0.0
guardrails:
  - never delete
  - preserve links
```
'''


def test_accepts_only_observed_notion_normalizations():
    result = compare_markdown(EXPECTED, NORMALIZED, title="sample-skill")
    assert result["comparator_version"] == 5
    assert {"removed_matching_leading_h1", "notion_html_table", "plain_text_fence_label", "escaped_emphasis_markers"} <= set(result["normalization_rules"])


def test_accepts_txt_fence_label_with_exact_code():
    result = compare_markdown(EXPECTED, NORMALIZED.replace("```plain text", "```txt"), title="sample-skill")
    assert "plain_text_fence_label" in result["normalization_rules"]


def test_accepts_notion_filename_autolinks_and_escaped_home_marker():
    expected=EXPECTED.replace("Do **not** change", "Use generic_tool_loop.py and ~/.agents/config for $5 -> done. Do **not** change")
    actual=NORMALIZED.replace("Do not change", "Use generic_tool_[loop.py](http://loop.py) and \\~/.agents/config for \\$5 -\\> done. Do not change")
    compare_markdown(expected,actual,title="sample-skill")


@pytest.mark.parametrize("changed", [
    NORMALIZED.replace("Do not change", "Do change"),
    NORMALIZED.replace("https://example.com", "https://evil.example"),
    NORMALIZED.replace('run("exact")', 'run("other")'),
    NORMALIZED.replace("<td>Read</td><td>`tool get <id>`</td>", "<td>`tool get <id>`</td><td>Read</td>"),
    NORMALIZED.replace("\t- Nested", "- Nested"),
    NORMALIZED.replace("never delete", "delete freely"),
])
def test_rejects_semantic_changes(changed):
    with pytest.raises(FidelityError):
        compare_markdown(EXPECTED, changed, title="sample-skill")


def test_rejects_unsupported_markup():
    changed = NORMALIZED.replace("<table header-row=\"true\">", "<table header-row=\"true\"><script>")
    with pytest.raises(FidelityError, match="unsupported_table_markup"):
        compare_markdown(EXPECTED, changed, title="sample-skill")


def test_rejects_duplicate_metadata_keys():
    changed = NORMALIZED.replace("version: 1.0.0", "version: 1.0.0\nversion: 2.0.0")
    with pytest.raises(FidelityError, match="duplicate_metadata_key"):
        compare_markdown(EXPECTED, changed, title="sample-skill")


def test_rejects_unexplained_heading_removal():
    expected = EXPECTED.replace("Do **not**", "## Guardrail\n\nDo **not**")
    with pytest.raises(FidelityError):
        compare_markdown(expected, NORMALIZED, title="sample-skill")


def test_accepts_notion_export_list_and_bold_wrappers():
    expected = EXPECTED.replace("- First\n  continuation", "- **First continuation** starts here,\n  and ends here")
    actual = NORMALIZED.replace("- First\n\tcontinuation", "- **First**<b> continuation starts here,</b>\n  <p>\n  and ends here\n  </p>")
    result = compare_markdown(expected, actual, title="sample-skill")
    assert {"bold_wrapper", "list_paragraph_wrapper"} <= set(result["normalization_rules"])


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("  <p>", "<p>"),
    lambda text: text.replace("<b>", "<b class=\"x\">"),
    lambda text: text.replace("and ends here", "and may delete here"),
    lambda text: text.replace("  </p>", "- Moved\n  </p>"),
    lambda text: text.replace("</b>", "</b><script>x</script>"),
])
def test_rejects_unsafe_export_wrappers(mutation):
    expected = EXPECTED.replace("- First\n  continuation", "- **First continuation** starts here,\n  and ends here")
    actual = NORMALIZED.replace("- First\n\tcontinuation", "- **First**<b> continuation starts here,</b>\n  <p>\n  and ends here\n  </p>")
    with pytest.raises(FidelityError):
        compare_markdown(expected, mutation(actual), title="sample-skill")


def test_inline_code_asterisks_are_never_formatting():
    expected = EXPECTED.replace("Do **not** change [the link](https://example.com). Try *\"this exact thing\"*.", "Run `echo * *.txt`.")
    changed = expected.replace("`echo * *.txt`", "`echo .txt`")
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill")


def test_inline_code_whitespace_is_exact():
    expected = EXPECTED.replace("Do **not** change [the link](https://example.com). Try *\"this exact thing\"*.", "Run `printf 'a  b'`.")
    changed = expected.replace("`printf 'a  b'`", "`printf 'a b'`")
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill")


def test_literal_wildcards_are_never_emphasis():
    expected = EXPECTED.replace("Do **not** change [the link](https://example.com). Try *\"this exact thing\"*.", "Match files *.py and *.txt.")
    changed = expected.replace("*.py and *.txt", ".py and .txt")
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill")


def test_metadata_block_scalar_blank_lines_are_significant():
    expected = EXPECTED.replace("version: 1.0.0", "version: 1.0.0\nnotes: |\n  first\n\n  third")
    changed = expected.replace("  first\n\n  third", "  first\n  third")
    with pytest.raises(FidelityError, match="metadata_semantic_mismatch"):
        compare_markdown(expected, changed, title="sample-skill")


def test_metadata_scalar_types_are_significant():
    expected = EXPECTED.replace("version: 1.0.0", "version: 1.0.0\nenabled: true")
    changed = expected.replace("enabled: true", "enabled: 1")
    with pytest.raises(FidelityError, match="metadata_semantic_mismatch"):
        compare_markdown(expected, changed, title="sample-skill")


def test_two_paragraphs_cannot_be_merged_into_one_line():
    expected = EXPECTED.replace("Do **not** change [the link](https://example.com). Try *\"this exact thing\"*.", "First paragraph.\n\nSecond paragraph.")
    changed = expected.replace("First paragraph.\n\nSecond paragraph.", "First paragraph. Second paragraph.")
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill")


def test_accepts_three_space_ordered_list_continuation_normalized_by_notion():
    expected = EXPECTED.replace(
        "- First\n  continuation\n  - Nested",
        "1. First ordered item starts here and\n   continues on the next source line.\n2. Second item.",
    )
    actual = NORMALIZED.replace(
        "- First\n\tcontinuation\n\t- Nested",
        "1. First ordered item starts here and continues on the next source line.\n2. Second item.",
    )
    compare_markdown(expected, actual, title="sample-skill", profile="page")


def test_accepts_notion_flattening_fenced_code_nested_under_list():
    expected = EXPECTED.replace(
        "```text\nif allowed:\n    run(\"exact\")\n```",
        "  ```bash\n  python3 scripts/check.py\n  ```",
    )
    actual = NORMALIZED.replace(
        "```plain text\nif allowed:\n    run(\"exact\")\n```",
        "```bash\npython3 scripts/check.py\n```",
    )
    compare_markdown(expected, actual, title="sample-skill", profile="page")


def test_accepts_notion_tab_container_for_fence_nested_under_list():
    expected = EXPECTED.replace(
        "```text\nif allowed:\n    run(\"exact\")\n```",
        "  ```bash\n  python3 scripts/check.py\n  ```",
    )
    actual = NORMALIZED.replace(
        "```plain text\nif allowed:\n    run(\"exact\")\n```",
        "\t```bash\npython3 scripts/check.py\n\t```",
    )
    compare_markdown(expected, actual, title="sample-skill", profile="page")


def test_source_tab_indented_fence_is_not_reinterpreted_as_notion_container():
    expected = EXPECTED.replace(
        '```text\nif allowed:\n    run("exact")\n```',
        '\t```sh\necho KEEP\n\t```',
    )
    actual = expected.replace('\t```sh\necho KEEP\n\t```', '```sh\necho KEEP\n```')
    with pytest.raises(FidelityError, match="unsupported_fence_indentation"):
        compare_markdown(expected, actual, title="sample-skill", profile="page")


@pytest.mark.parametrize("profile", ["page", "export"])
@pytest.mark.parametrize("block", ["> Never delete", "---"])
@pytest.mark.parametrize("source,indent", [("1. Follow policy", "   "), ("- Follow policy", "  ")])
def test_list_block_construct_cannot_be_flattened_into_paragraph_text(profile, block, source, indent):
    expected = EXPECTED.replace(
        "- First\n  continuation\n  - Nested",
        f"{source}\n{indent}{block}",
    )
    changed = expected.replace(f"{source}\n{indent}{block}", f"{source} {block}")
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill", profile=profile)


@pytest.mark.parametrize("profile", ["page", "export"])
def test_four_space_literal_code_cannot_be_treated_as_fenced_code(profile):
    expected = EXPECTED.replace(
        '```text\nif allowed:\n    run("exact")\n```',
        '    ```sh\n    echo KEEP\n    ```',
    )
    changed = expected.replace('    ```sh\n    echo KEEP\n    ```', '```sh\necho KEEP\n```')
    with pytest.raises(FidelityError, match="unsupported_fence_indentation"):
        compare_markdown(expected, changed, title="sample-skill", profile=profile)


@pytest.mark.parametrize("profile", ["page", "export"])
def test_fence_dedent_removes_up_to_opening_indent_per_line(profile):
    expected = EXPECTED.replace(
        '```text\nif allowed:\n    run("exact")\n```',
        '  ```sh\n if ok:\n    run()\n  ```',
    )
    actual = expected.replace('  ```sh\n if ok:\n    run()\n  ```', '```sh\nif ok:\n  run()\n```')
    compare_markdown(expected, actual, title="sample-skill", profile=profile)


@pytest.mark.parametrize("profile", ["page", "export"])
def test_fence_partial_dedent_does_not_accept_changed_indentation(profile):
    expected = EXPECTED.replace(
        '```text\nif allowed:\n    run("exact")\n```',
        '  ```sh\n if ok:\n    run()\n  ```',
    )
    changed = expected.replace('  ```sh\n if ok:\n    run()\n  ```', '```sh\n if ok:\n  run()\n```')
    with pytest.raises(FidelityError):
        compare_markdown(expected, changed, title="sample-skill", profile=profile)
