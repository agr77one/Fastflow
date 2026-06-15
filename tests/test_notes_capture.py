from __future__ import annotations

import re

import notes
import pytest

# ---------- _safe_category (path-traversal guard) ----------------------------

@pytest.mark.parametrize("bad", ["", "   ", "..", "../etc", "a/../b", "..\\..\\windows", "/", "a/./b"])
def test_safe_category_rejects_traversal(bad):
    with pytest.raises(ValueError, match="invalid note category"):
        notes._safe_category(bad)


@pytest.mark.parametrize(("raw", "expected"), [
    ("work/technical", "work/technical"),
    ("work\\technical", "work/technical"),   # backslashes normalized
    ("/research/", "research"),               # surrounding slashes stripped
    ("ideas", "ideas"),
])
def test_safe_category_accepts_and_normalizes(raw, expected):
    assert notes._safe_category(raw) == expected


# ---------- _parse_categorize_json (robust LLM-output parsing) ---------------

def test_parse_categorize_json_plain_object():
    assert notes._parse_categorize_json('{"category":"ideas"}') == {"category": "ideas"}


def test_parse_categorize_json_strips_code_fences():
    raw = '```json\n{"category":"research","title":"X"}\n```'
    assert notes._parse_categorize_json(raw) == {"category": "research", "title": "X"}


def test_parse_categorize_json_recovers_embedded_object():
    raw = 'Sure, here you go:\n{"category":"work/technical"} hope that helps!'
    assert notes._parse_categorize_json(raw) == {"category": "work/technical"}


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2, 3]", "null"])
def test_parse_categorize_json_returns_none_on_garbage(raw):
    assert notes._parse_categorize_json(raw) is None


# ---------- HTML extraction (stdlib parser path) -----------------------------

def test_text_extractor_strips_scripts_and_keeps_title():
    html = (
        "<html><head><title>Page Title</title></head>"
        "<body><p>Visible text here.</p>"
        "<script>stealMe()</script><style>.x{color:red}</style></body></html>"
    )
    parser = notes._TextExtractor()
    parser.feed(html)
    assert parser.title == "Page Title"
    assert "Visible text here." in parser.text
    assert "stealMe" not in parser.text
    assert "color:red" not in parser.text


# ---------- frontmatter round-trip + serialization ---------------------------

def test_split_frontmatter_title_extracts_and_splits():
    text = '---\ntitle: "My Note"\ncategory: ideas\n---\n\nThe body begins here.'
    title, body = notes._split_frontmatter_title(text)
    assert title == "My Note"
    assert body == "The body begins here."


def test_split_frontmatter_title_no_frontmatter():
    title, body = notes._split_frontmatter_title("Just a plain note.")
    assert title == ""
    assert body == "Just a plain note."


def test_yaml_frontmatter_quotes_and_types():
    out = notes._yaml_frontmatter({
        "title": 'He said "hi": done',
        "count": 3,
        "flag": True,
        "missing": None,
        "tags": ["a", "b"],
    })
    assert out.startswith("---\n") and out.endswith("\n---")
    # String with embedded quote/colon must be JSON-quoted so YAML can't misparse it.
    assert '"He said \\"hi\\": done"' in out
    assert "count: 3" in out
    assert "flag: true" in out
    assert "missing: null" in out
    assert 'tags: ["a", "b"]' in out


# ---------- slug / timestamp helpers -----------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("Hello, World!", "hello-world"),
    ("  spaced  out  ", "spaced-out"),
    ("", "untitled"),
    ("!!!", "untitled"),
])
def test_slugify(raw, expected):
    assert notes._slugify(raw) == expected


def test_slugify_truncates_without_trailing_dash():
    s = notes._slugify("a" * 40 + " " + "b" * 40, max_len=20)
    assert len(s) <= 20
    assert not s.endswith("-")


def test_timestamp_prefix_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", notes._timestamp_prefix(0))


# ---------- _build_body ------------------------------------------------------

def test_build_body_quotes_capture_and_adds_link():
    body = notes._build_body(
        text="line one\nline two",
        url="https://example.com/article",
        fetched=None,
        categorized=None,
    )
    assert "## Captured" in body
    assert "> line one" in body
    assert "> line two" in body
    assert "[Read original →](https://example.com/article)" in body
    assert body.endswith("\n")


# ---------- capture_note (synchronous stub + URL detection) ------------------

def test_capture_note_writes_selection_stub(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)
    # Keep the background categorizer (network/LLM) out of the unit test.
    monkeypatch.setattr(notes, "_categorize_in_background", lambda *a, **k: None)

    out = notes.capture_note("Some selected text", source_app="chrome.exe")

    assert out["is_url_only"] is False
    stub = vault / "inbox"
    written = list(stub.glob("*.md"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert 'title: "(categorizing…)"' in content
    assert 'category: "inbox"' in content
    assert 'captured_via: "selection"' in content
    assert "Some selected text" in content


def test_capture_note_detects_url_only(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)
    monkeypatch.setattr(notes, "_categorize_in_background", lambda *a, **k: None)

    out = notes.capture_note("https://example.com/article")

    assert out["is_url_only"] is True
    written = list((vault / "inbox").glob("*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert 'captured_via: "url"' in text
    assert "https://example.com/article" in text
