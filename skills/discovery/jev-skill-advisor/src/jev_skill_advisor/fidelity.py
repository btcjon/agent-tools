"""Strict semantic comparison for Notion-normalized pilot Markdown."""
from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

import yaml


VERSION = 5
METADATA_HEADING = "## Preserved source metadata"
METADATA_NOTICE = "The following metadata is part of the canonical skill contract and remains authoritative for this pilot."


class FidelityError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise FidelityError("duplicate_metadata_key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _metadata_parts(markdown):
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    positions = [index for index, line in enumerate(lines) if line.strip() == METADATA_HEADING]
    if len(positions) != 1:
        raise FidelityError("metadata_heading_count")
    index = positions[0]
    tail = lines[index + 1 :]
    cursor = 0
    while cursor < len(tail) and not tail[cursor].strip(): cursor += 1
    if cursor >= len(tail) or tail[cursor].strip() != METADATA_NOTICE:
        raise FidelityError("invalid_metadata_appendix")
    cursor += 1
    while cursor < len(tail) and not tail[cursor].strip(): cursor += 1
    if cursor >= len(tail) or tail[cursor].strip() != "```yaml":
        raise FidelityError("invalid_metadata_appendix")
    cursor += 1; start = cursor
    while cursor < len(tail) and tail[cursor].strip() != "```": cursor += 1
    if cursor >= len(tail) or any(line.strip() for line in tail[cursor + 1 :]):
        raise FidelityError("invalid_metadata_appendix")
    if any(line.strip().startswith("```") for line in tail[start:cursor]):
        raise FidelityError("invalid_metadata_fence")
    raw = "\n".join(tail[start:cursor])
    try:
        value = yaml.load(raw, Loader=_UniqueLoader)
    except FidelityError:
        raise
    except yaml.YAMLError as exc:
        raise FidelityError("invalid_metadata_yaml") from exc
    if not isinstance(value, dict):
        raise FidelityError("metadata_must_be_mapping")
    return lines[:index], value


_PROTECTED_INLINE = re.compile(r"(`+)[^`]*\1|\[[^\]]*\]\([^)]*\)")


def _notion_autolinks(text):
    extensions=r"(?:py|sh|js|ts|tsx|jsx|md|txt|json|ya?ml|toml|html?|css|ai)"
    def unwrap(match):
        label,url=match.group(1),match.group(2)
        return label if label==url and re.fullmatch(rf"[A-Za-z0-9_.-]+\.{extensions}",label,re.I) else match.group(0)
    text=re.sub(r"\[([^\]]+)\]\(http://([^)]+)\)",unwrap,text)
    return re.sub(r"\\([~$>])",r"\1",text)


def _inline(text):
    text=_notion_autolinks(text)
    output = []; cursor = 0
    for match in _PROTECTED_INLINE.finditer(text):
        output.append(re.sub(r"\s+", " ", text[cursor:match.start()]))
        output.append(match.group())
        cursor = match.end()
    output.append(re.sub(r"\s+", " ", text[cursor:]))
    return "".join(output).strip()


def _transform_unprotected(text, transform):
    output = []; cursor = 0
    for match in _PROTECTED_INLINE.finditer(text):
        output.append(transform(text[cursor:match.start()]))
        output.append(match.group())
        cursor = match.end()
    output.append(transform(text[cursor:]))
    return "".join(output)


def _shield_inline(text):
    values = []
    def replace(match):
        values.append(match.group())
        return f"\ue000{len(values) - 1}\ue001"
    return _PROTECTED_INLINE.sub(replace, text), values


def _restore_inline(text, values):
    for index, value in enumerate(values):
        text = text.replace(f"\ue000{index}\ue001", value)
    return text


def _title_key(text):
    return " ".join(re.sub(r"[-_]", " ", _inline(text)).casefold().split())


def _normalize_emphasis(lines, *, global_blocks=False):
    output = list(lines); rules = set()
    def normalize_chunk(chunk):
        def normalize(segment):
            collapsed, joins = re.subn(r"(?:(?<=\S)\*{4}(?=\s?\S)|\*{4}(?=\ue000)|(?<=\ue001)\*{4})", "", segment)
            if joins:
                segment = collapsed; rules.add("formatting_markers")
            bold = re.compile(r"(?<![\w*])(?:\\?\*\\?\*)(?=[^\s.])(.+?)(?<=[^\s])(?:\\?\*\\?\*)(?=$|[\s.,;:!?\)])", re.S)
            italic = re.compile(r"(?<![\w*])(?:\\?\*)(?![*.\s])(.+?)(?<=[^\s])(?:\\?\*)(?=$|[\s.,;:!?\)])", re.S)
            for pattern in (bold, italic):
                while True:
                    match = pattern.search(segment)
                    if not match: break
                    markers = match.group(0)
                    if "\\*" in markers: rules.add("escaped_emphasis_markers")
                    segment = segment[:match.start()] + match.group(1) + segment[match.end():]
                    rules.add("formatting_markers")
            return segment
        shielded, protected = _shield_inline(chunk)
        return _restore_inline(normalize(shielded), protected)
    if global_blocks:
        start = 0; fenced = False
        for index, line in enumerate(output + ["```"]):
            if re.fullmatch(r"\s*```.*", line):
                if not fenced and index > start:
                    output[start:index] = normalize_chunk("\n".join(output[start:index])).split("\n")
                fenced = not fenced
                if not fenced: start = index + 1
        return output, rules
    index = 0; fenced = False
    while index < len(output):
        line = output[index]
        if re.fullmatch(r"\s*```.*", line):
            fenced = not fenced; index += 1; continue
        if fenced or not line.strip(): index += 1; continue
        left = index; listed = re.match(r"([ \t]*)(?:[-+*]|\d+[.)])\s+", line)
        index += 1
        if listed:
            base = _indent(listed.group(1))
            while index < len(output):
                following = output[index]
                if not following.strip(): break
                nested = re.match(r"([ \t]*)(?:[-+*]|\d+[.)])\s+", following)
                leading = re.match(r"^[ \t]*", following).group()
                # CommonMark ordered-list continuations are commonly indented
                # three spaces. They are paragraph continuation text, not a
                # nested list level, so compare raw visual width here.
                if nested or not leading or _indent_width(leading) <= base * 2: break
                index += 1
        else:
            while index < len(output):
                following = output[index]
                stripped = following.strip()
                if (not stripped or re.match(r"#{1,6}\s+", stripped)
                        or re.match(r"(?:[-+*]|\d+[.)])\s+", stripped)
                        or stripped.startswith(("```", "<table", "|"))): break
                index += 1
        chunk = "\n".join(output[left:index])
        output[left:index] = normalize_chunk(chunk).split("\n")
    # Slice replacement above can move the cursor across a later line when
    # Notion changes list/fence indentation. Make the pass idempotent so no
    # residual emphasis marker escapes normalization.
    fenced = False
    for position, line in enumerate(output):
        if re.fullmatch(r"\s*```.*", line):
            fenced = not fenced
            continue
        if not fenced:
            output[position] = normalize_chunk(line)
    return output, rules


def _normalize_export_wrappers(lines):
    output = []; rules = set(); active_list = False; paragraph = False; paragraph_indent = None; fenced = False
    for line in lines:
        if re.fullmatch(r"\s*```.*", line):
            fenced = not fenced; output.append(line); continue
        if fenced:
            output.append(line); continue
        stripped = line.strip(); leading = re.match(r"^[ \t]*", line).group()
        if stripped == "<p>":
            if paragraph or not active_list or not leading:
                raise FidelityError("invalid_list_paragraph_wrapper")
            paragraph = True; paragraph_indent = leading; rules.add("list_paragraph_wrapper"); continue
        if stripped == "</p>":
            if not paragraph or leading != paragraph_indent:
                raise FidelityError("invalid_list_paragraph_wrapper")
            paragraph = False; paragraph_indent = None; continue
        if paragraph and (not leading or re.match(r"\s*(?:[-+*]|\d+[.)])\s+", line)):
            raise FidelityError("list_paragraph_crosses_structure")
        if "<b" in line or "</b" in line:
            count = 0
            def strip_bold(segment):
                nonlocal count
                if re.search(r"<b(?:\s|>)", segment) and not re.fullmatch(r".*<b>[^<>]*</b>.*", segment):
                    raise FidelityError("invalid_bold_wrapper")
                segment, found = re.subn(r"<b>([^<>]*)</b>", lambda match: match.group(1), segment)
                count += found
                if "<b" in segment or "</b" in segment: raise FidelityError("invalid_bold_wrapper")
                return segment
            replaced = _transform_unprotected(line, strip_bold)
            if count == 0: raise FidelityError("invalid_bold_wrapper")
            line = replaced; stripped = line.strip(); rules.add("bold_wrapper")
        if re.match(r"\s*(?:[-+*]|\d+[.)])\s+", line):
            active_list = True
        elif stripped and not leading:
            active_list = False
        output.append(line)
    if paragraph: raise FidelityError("unterminated_list_paragraph_wrapper")
    return output, rules


class _NotionTable(HTMLParser):
    allowed = {"table", "tr", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.header = False

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed:
            raise FidelityError("unsupported_table_markup")
        attributes = dict(attrs)
        if tag == "table":
            if set(attributes) - {"header-row"} or attributes.get("header-row") not in {None, "true"}:
                raise FidelityError("unsupported_table_attributes")
            self.header = attributes.get("header-row") == "true"
        elif attrs:
            raise FidelityError("unsupported_table_attributes")
        if tag == "tr":
            if self.row is not None: raise FidelityError("nested_table_row")
            self.row = []
        elif tag == "td":
            if self.row is None or self.cell is not None: raise FidelityError("invalid_table_cell")
            self.cell = []

    def handle_endtag(self, tag):
        if tag not in self.allowed: raise FidelityError("unsupported_table_markup")
        if tag == "td":
            if self.cell is None: raise FidelityError("invalid_table_cell")
            self.row.append(_inline("".join(self.cell))); self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None: raise FidelityError("invalid_table_row")
            self.rows.append(tuple(self.row)); self.row = None

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
        elif data.strip(): raise FidelityError("text_outside_table_cell")

    def handle_startendtag(self, tag, attrs): raise FidelityError("unsupported_table_markup")
    def handle_comment(self, data): raise FidelityError("unsupported_table_markup")


def _gfm_table(lines, index):
    if index + 1 >= len(lines) or "|" not in lines[index]: return None
    separator = lines[index + 1].strip()
    cells = [cell.strip() for cell in separator.strip("|").split("|")]
    if not cells or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells): return None
    rows = []
    cursor = index
    while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
        if cursor != index + 1:
            row = tuple(_inline(cell) for cell in lines[cursor].strip().strip("|").split("|"))
            rows.append(row)
        cursor += 1
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise FidelityError("invalid_gfm_table")
    return ("table", True, tuple(rows)), cursor


def _html_table(lines, index):
    if not lines[index].lstrip().startswith("<table"): return None
    collected = []
    cursor = index
    while cursor < len(lines):
        collected.append(lines[cursor])
        if "</table>" in lines[cursor]: break
        cursor += 1
    else: raise FidelityError("unterminated_html_table")
    source = "\n".join(collected)
    # Angle-bracket placeholders inside Markdown code spans are text, not HTML.
    source = re.sub(r"`([^`]+)`", lambda match: "`" + html.escape(match.group(1)) + "`", source)
    parser = _NotionTable(); parser.feed(source); parser.close()
    if parser.row is not None or parser.cell is not None or not parser.rows:
        raise FidelityError("invalid_html_table")
    if any(len(row) != len(parser.rows[0]) for row in parser.rows):
        raise FidelityError("invalid_html_table")
    return ("table", parser.header, tuple(parser.rows)), cursor + 1


def _indent(value):
    width = sum(2 if char == "\t" else 1 for char in value)
    if width % 2: raise FidelityError("unsupported_list_indentation")
    return width // 2


def _indent_width(value):
    """Measure continuation indentation without imposing nested-list syntax."""
    return sum(2 if char == "\t" else 1 for char in value)


def _is_block_construct(text):
    """Return whether indented text starts a block, not list paragraph text."""
    stripped = text.strip()
    return bool(
        stripped.startswith(("```", "~~~", "#", "<", "|", ">"))
        or re.fullmatch(r"(?:\*\s*){3,}", stripped)
        or re.fullmatch(r"(?:-\s*){3,}", stripped)
        or re.fullmatch(r"(?:_\s*){3,}", stripped)
    )


def _structure(lines, title, *, merge_wrapped=False, allow_notion_fence_tab=False):
    lines, wrapper_rules = _normalize_export_wrappers(lines)
    lines, emphasis_rules = _normalize_emphasis(lines, global_blocks=merge_wrapped)
    tokens = []; rules = set(emphasis_rules) | set(wrapper_rules); index = 0
    while index < len(lines) and not lines[index].strip(): index += 1
    if index < len(lines) and re.fullmatch(r"#\s+.*", lines[index].strip()):
        heading = lines[index].strip()[2:].strip()
        if _title_key(heading) == _title_key(title):
            tokens.append(("title", _title_key(title))); rules.add("leading_title_h1"); index += 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            rules.add("blank_lines"); index += 1; continue
        if line.lstrip().startswith("<"):
            table = _html_table(lines, index)
            if table is None: raise FidelityError("unsupported_html_markup")
            token, index = table; tokens.append(token); rules.add("notion_html_table"); continue
        table = _gfm_table(lines, index)
        if table:
            token, index = table; tokens.append(token); rules.add("gfm_table"); continue
        fence = re.fullmatch(r"(\s*)```(.*)", line)
        if fence:
            fence_indent = fence.group(1)
            notion_tab = fence_indent == "\t" and allow_notion_fence_tab
            if ("\t" in fence_indent and not notion_tab) or (not notion_tab and len(fence_indent) > 3):
                raise FidelityError("unsupported_fence_indentation")
            # Notion represents a source fence nested two spaces under a list
            # with one tab and has already removed that container indentation.
            fence_width = 0 if notion_tab else len(fence_indent)
            if notion_tab:
                rules.add("tab_list_indentation")
            label = fence.group(2).strip()
            normalized = "text" if label in {"text", "plain text", "txt"} else label
            if label in {"plain text", "txt"}: rules.add("plain_text_fence_label")
            body = []; index += 1
            while index < len(lines) and not re.fullmatch(r"\s*```\s*", lines[index]):
                value = lines[index].rstrip("\r")
                if fence_width:
                    if value.startswith("\t"):
                        raise FidelityError("unsupported_fence_body_indentation")
                    leading_spaces = len(value) - len(value.lstrip(" "))
                    value = value[min(fence_width, leading_spaces):]
                body.append(value); index += 1
            if index >= len(lines): raise FidelityError("unterminated_code_fence")
            tokens.append(("code", normalized, tuple(body))); index += 1; continue
        heading = re.fullmatch(r"(#{1,6})\s+(.+)", line.strip())
        if heading:
            tokens.append(("heading", len(heading.group(1)), _inline(heading.group(2)))); index += 1; continue
        listed = re.fullmatch(r"([ \t]*)([-+*]|\d+[.)])\s+(.+)", line)
        if listed:
            marker = "unordered" if not listed.group(2)[0].isdigit() else ("ordered", int(re.match(r"\d+", listed.group(2)).group()))
            level = _indent(listed.group(1)); content = listed.group(3)
            # A three-space continuation under an ordered item is valid
            # CommonMark and Notion folds it into the list paragraph.
            while index + 1 < len(lines):
                following = lines[index + 1]
                leading = re.match(r"^[ \t]*", following).group()
                width = _indent_width(leading)
                if (not following.strip() or _is_block_construct(following)
                        or re.match(r"[ \t]*(?:[-+*]|\d+[.)])\s+", following)
                        or width <= level * 2):
                    break
                content += " " + following[len(leading):]
                index += 1
            tokens.append(("list", level, marker, _inline(content)))
            if "\t" in listed.group(1): rules.add("tab_list_indentation")
            index += 1; continue
        leading = re.match(r"^[ \t]*", line).group()
        tokens.append(("line", _indent(leading), _inline(line[len(leading):])))
        if "\t" in leading: rules.add("tab_list_indentation")
        index += 1
    if not merge_wrapped:
        return tuple(tokens), rules
    merged = []
    for token in tokens:
        if token[0] == "line" and merged and merged[-1][0] == "line" and merged[-1][1] == token[1]:
            merged[-1] = ("line", token[1], _inline(merged[-1][2] + " " + token[2]))
        elif (token[0] == "line" and merged and merged[-1][0] == "list"
                and token[1] > merged[-1][1] and not _is_block_construct(token[2])):
            prior = merged[-1]
            merged[-1] = (prior[0], prior[1], prior[2], _inline(prior[3] + " " + token[2]))
        else:
            merged.append(token)
    return tuple(merged), rules


def _typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if len(left) != len(right): return False
        right_items = list(right.items()); used = set()
        for left_key, left_value in left.items():
            match = next((index for index, (right_key, _) in enumerate(right_items)
                          if index not in used and type(left_key) is type(right_key) and left_key == right_key), None)
            if match is None or not _typed_equal(left_value, right_items[match][1]): return False
            used.add(match)
        return True
    if isinstance(left, list):
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def _plain_paragraphs(lines):
    """Return source paragraphs for export reflow boundary checks."""
    lines, _ = _normalize_export_wrappers(lines)
    lines, _ = _normalize_emphasis(lines)
    paragraphs = []; current = []; fenced = False; in_table = False
    for line in lines + [""]:
        stripped = line.strip()
        if re.fullmatch(r"```.*", stripped):
            if current: paragraphs.append(_inline(" ".join(current))); current = []
            fenced = not fenced; continue
        if fenced: continue
        if stripped.startswith("<table"): in_table = True
        if in_table:
            if stripped == "</table>": in_table = False
            continue
        if (not stripped or re.match(r"#{1,6}\s+", stripped)
                or re.match(r"(?:[-+*]|\d+[.)])\s+", stripped)
                or ("|" in stripped and (stripped.startswith("|") or stripped.endswith("|")))):
            if current: paragraphs.append(_inline(" ".join(current))); current = []
            continue
        if re.match(r"[ \t]+", line):
            continue
        current.append(stripped)
    return paragraphs


def _ensure_export_lines_do_not_cross_paragraphs(expected_lines, actual_lines):
    expected = _plain_paragraphs(expected_lines)
    if not expected: return
    actual, _ = _normalize_export_wrappers(actual_lines)
    actual, _ = _normalize_emphasis(actual, global_blocks=True)
    cursor = 0; fenced = False; in_table = False
    for line in actual:
        text = _inline(line)
        if text.startswith("```"): fenced = not fenced; continue
        if fenced: continue
        if text.startswith("<table"): in_table = True; continue
        if in_table:
            if text == "</table>": in_table = False
            continue
        if (not text or text.startswith("#") or re.match(r"(?:[-+*]|\d+[.)])\s+", text)
                or re.match(r"^[ \t]+", line)
                or ("|" in text and (text.startswith("|") or text.endswith("|")))): continue
        match = next((index for index in range(cursor, len(expected)) if text in expected[index]), None)
        if match is None: raise FidelityError("export_paragraph_boundary_mismatch")
        cursor = match


def compare_markdown(expected, actual, *, title, profile="page"):
    if profile not in {"page", "export"}: raise FidelityError("invalid_comparison_profile")
    expected_body, expected_metadata = _metadata_parts(expected)
    actual_body, actual_metadata = _metadata_parts(actual)
    if not _typed_equal(expected_metadata, actual_metadata):
        raise FidelityError("metadata_semantic_mismatch")
    if profile == "export": _ensure_export_lines_do_not_cross_paragraphs(expected_body, actual_body)
    expected_structure, expected_rules = _structure(expected_body, title, merge_wrapped=profile == "export")
    actual_structure, actual_rules = _structure(
        actual_body,
        title,
        merge_wrapped=profile == "export",
        allow_notion_fence_tab=True,
    )
    # A matching title token is optional only on the Notion side.
    if expected_structure and expected_structure[0] == ("title", _title_key(title)) and (not actual_structure or actual_structure[0] != expected_structure[0]):
        expected_structure = expected_structure[1:]
        actual_rules.add("removed_matching_leading_h1")
    if expected_structure != actual_structure:
        for index, (left, right) in enumerate(zip(expected_structure, actual_structure)):
            if left != right: raise FidelityError(f"content_structure_mismatch_at_{index}")
        raise FidelityError("content_structure_length_mismatch")
    rules = sorted((actual_rules | expected_rules) & {"blank_lines", "removed_matching_leading_h1", "tab_list_indentation", "notion_html_table", "gfm_table", "plain_text_fence_label", "escaped_emphasis_markers", "formatting_markers", "list_paragraph_wrapper", "bold_wrapper"})
    return {"comparator_version": VERSION, "expected_sha256": _hash(expected), "actual_sha256": _hash(actual), "normalization_rules": sorted(set(rules))}
