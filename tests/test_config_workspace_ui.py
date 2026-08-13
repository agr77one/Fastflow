from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "scripts" / "ui" / "web"


def test_config_is_sectioned_instead_of_one_long_grid():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    config = html.split('id="tab-config"', 1)[1].split(
        'id="tab-benchmark"',
        1,
    )[0]

    assert 'id="config-sections"' in config
    assert 'role="tablist"' in config
    assert 'id="config-grid"' in config
    for section in (
        "essentials",
        "models",
        "notes",
        "prompts",
        "notifications",
        "meetings",
        "advanced",
    ):
        assert f'data-config-section="{section}"' in config
    assert 'id="config-save"' in config
    assert 'id="config-revert"' in config
    assert 'class="config-save-bar"' in config


def test_config_sections_and_collapsed_cards_persist_locally():
    app = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'const CONFIG_SECTION_KEY = "flowkey.config.section.v1"' in app
    assert 'const CONFIG_COLLAPSED_KEY = "flowkey.config.collapsed.v1"' in app
    assert "function setConfigSection(section, focus = false)" in app
    assert "function setConfigCardCollapsed(card, collapsed)" in app
    assert 'event.key === "ArrowRight"' in app
    assert 'event.key === "ArrowLeft"' in app
    assert 'button.setAttribute("aria-expanded", String(!collapsed))' in app
    assert "localStorage.setItem(CONFIG_SECTION_KEY, selected)" in app
    assert ".innerHTML" not in app


def test_config_workspace_is_sticky_and_responsive():
    css = (WEB / "styles.css").read_text(encoding="utf-8")

    assert ".config-save-bar {" in css
    assert "position: sticky;" in css
    assert ".config-card.is-collapsed > :not(.config-card-heading)" in css
    assert "@media (max-width: 720px)" in css
    assert ".config-sections" in css
