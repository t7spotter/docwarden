from docwarden import md


def test_headings_are_offset_below_the_page_title():
    assert '<h3 dir="auto">Flow</h3>' in md.render("## Flow")


def test_paragraphs_join_wrapped_lines():
    assert md.render("one\ntwo\n\nthree").count("<p") == 2


def test_inline_markup():
    rendered = md.render("a `code` and **bold** and [link](https://example.com)")
    assert "<code>code</code>" in rendered
    assert "<strong>bold</strong>" in rendered
    assert '<a href="https://example.com"' in rendered


def test_html_in_source_is_escaped():
    assert "<script>" not in md.render("<script>alert(1)</script>")


def test_code_spans_keep_their_contents_literal():
    assert "<code>&lt;div&gt;</code>" in md.render("`<div>`")


def test_ordered_and_unordered_lists():
    assert "<ol" in md.render("1. first\n2. second")
    assert "<ul" in md.render("- first\n- second")


def test_continuation_lines_join_their_item():
    rendered = md.render("- first line\n  continues here\n- second")
    assert "first line continues here" in rendered
    assert rendered.count("<li>") == 2


def test_nested_list_is_kept_inside_its_parent_item():
    rendered = md.render("1. outer:\n   - inner one\n   - inner two\n2. next")
    assert "<ul" in rendered and "<ol" in rendered
    assert rendered.index("<ul") < rendered.index("</ol>")


def test_fenced_code_block():
    rendered = md.render("```json\n{\"a\": 1}\n```")
    assert '<code class="language-json">' in rendered
    assert "&quot;a&quot;" in rendered or '"a"' in rendered


def test_every_block_carries_dir_auto():
    # Right-to-left descriptions must lay themselves out without configuration.
    for source in ("plain text", "## heading", "- item"):
        assert 'dir="auto"' in md.render(source)


def test_empty_input_renders_nothing():
    assert md.render("") == ""
    assert md.render("   \n  ") == ""


def test_strip_produces_a_plain_summary():
    assert md.strip("## Title\n\nSome **bold** text") == "Title Some bold text"
    assert md.strip("x" * 500, limit=20).endswith("…")
