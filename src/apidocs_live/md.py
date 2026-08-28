"""A small Markdown subset renderer.

OpenAPI descriptions are Markdown, and the ones in this doc set use headings,
nested lists, bold and inline code. That is a narrow enough subset to render in
a hundred lines rather than take a dependency, and it lets every block carry
dir="auto", so right-to-left text lays itself out without configuration.
"""

from __future__ import annotations

import html
import re
import textwrap

_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def render(source: str, heading_offset: int = 1) -> str:
    """Render Markdown to HTML. Returns "" for empty input."""
    if not source or not source.strip():
        return ""
    lines = textwrap.dedent(source).splitlines()
    blocks, _ = _render_blocks(lines, 0, base_indent=0, heading_offset=heading_offset)
    return "\n".join(blocks)


def _render_blocks(lines: list[str], start: int, base_indent: int, heading_offset: int):
    out: list[str] = []
    paragraph: list[str] = []
    position = start

    def flush() -> None:
        if paragraph:
            out.append(f'<p dir="auto">{inline(" ".join(paragraph))}</p>')
            paragraph.clear()

    while position < len(lines):
        line = lines[position]

        if not line.strip():
            flush()
            position += 1
            continue

        indent = len(line) - len(line.lstrip())
        if indent < base_indent:
            break

        fence = _FENCE.match(line)
        if fence:
            flush()
            position += 1
            code: list[str] = []
            while position < len(lines) and not _FENCE.match(lines[position]):
                code.append(lines[position])
                position += 1
            position += 1  # closing fence
            language = f' class="language-{fence.group(1)}"' if fence.group(1) else ""
            body = html.escape("\n".join(textwrap.dedent("\n".join(code)).splitlines()))
            out.append(f"<pre><code{language}>{body}</code></pre>")
            continue

        heading = _HEADING.match(line.strip())
        if heading:
            flush()
            level = min(len(heading.group(1)) + heading_offset, 6)
            out.append(f'<h{level} dir="auto">{inline(heading.group(2))}</h{level}>')
            position += 1
            continue

        if _BULLET.match(line) or _ORDERED.match(line):
            flush()
            block, position = _render_list(lines, position, indent, heading_offset)
            out.append(block)
            continue

        paragraph.append(line.strip())
        position += 1

    flush()
    return out, position


def _render_list(lines: list[str], start: int, indent: int, heading_offset: int):
    ordered = bool(_ORDERED.match(lines[start]))
    items: list[str] = []
    position = start

    while position < len(lines):
        line = lines[position]
        if not line.strip():
            # A blank line ends the list unless the next line continues it.
            following = next((l for l in lines[position + 1 :] if l.strip()), "")
            if not following or (len(following) - len(following.lstrip())) < indent:
                break
            position += 1
            continue

        current_indent = len(line) - len(line.lstrip())
        if current_indent < indent:
            break

        match = _BULLET.match(line) or _ORDERED.match(line)
        if not match or current_indent > indent:
            break

        text = match.groups()[-1]
        position += 1

        # Gather this item's continuation lines and any nested list beneath it.
        continuation: list[str] = []
        while position < len(lines):
            nxt = lines[position]
            if not nxt.strip():
                following = next((l for l in lines[position + 1 :] if l.strip()), "")
                if not following or (len(following) - len(following.lstrip())) <= indent:
                    break
                continuation.append("")
                position += 1
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip())
            if nxt_indent <= indent:
                break
            continuation.append(nxt)
            position += 1

        items.append(_render_item(text, continuation, heading_offset))

    tag = "ol" if ordered else "ul"
    body = "\n".join(f"<li>{item}</li>" for item in items)
    return f'<{tag} dir="auto">\n{body}\n</{tag}>', position


def _render_item(text: str, continuation: list[str], heading_offset: int) -> str:
    plain: list[str] = []
    nested_start = None
    for position, line in enumerate(continuation):
        if _BULLET.match(line) or _ORDERED.match(line):
            nested_start = position
            break
        plain.append(line.strip())

    body = inline(" ".join([text, *[p for p in plain if p]]).strip())
    if nested_start is None:
        return body

    nested_lines = textwrap.dedent("\n".join(continuation[nested_start:])).splitlines()
    nested, _ = _render_blocks(nested_lines, 0, base_indent=0, heading_offset=heading_offset)
    return body + "\n" + "\n".join(nested)


def inline(text: str) -> str:
    """Escape, then apply inline Markdown. Code spans keep their literal text."""
    escaped = html.escape(text, quote=False)
    escaped = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    escaped = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" rel="noopener">{m.group(1)}</a>',
        escaped,
    )
    return escaped


def strip(source: str, limit: int = 200) -> str:
    """Plain-text summary of a Markdown block, for cards and meta tags."""
    text = re.sub(r"[`*#>]", "", source or "").strip()
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text
