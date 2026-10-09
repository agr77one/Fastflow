"""Static checks on the dashboard files (no browser): CSP-safety and markup/JS agreement."""

from __future__ import annotations

import json
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "scripts" / "ui" / "web"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_markup_has_no_inline_style_attributes():
    # The dashboard is served with CSP `default-src 'self'`, which blocks inline style
    # attributes -- they do nothing but log a console error, and the element silently
    # keeps its stylesheet width (the "to" between two time fields did exactly that).
    html = _read("index.html")
    assert not re.search(r"\sstyle\s*=", html), "inline style= is blocked by the dashboard CSP; use a class"


def test_the_classes_that_replaced_inline_styles_exist():
    html, css = _read("index.html"), _read("styles.css")
    for cls in ("label-inline", "warn-note"):
        assert re.search(rf'class="[^"]*\b{cls}\b', html), f"{cls} unused in markup"
        assert f".{cls}" in css, f"{cls} undefined in the stylesheet"


def test_every_meetings_element_the_script_touches_exists_in_the_markup():
    # app.js looks elements up by id; a typo or a removed element is a runtime TypeError
    # in the middle of an unrelated click handler, which no Python test would catch.
    html, app = _read("index.html"), _read("app.js")
    wanted = set(re.findall(r'\$\("(mtg-[a-z0-9-]+)"\)', app))
    assert wanted, "expected the script to reference meetings elements"
    missing = sorted(i for i in wanted if f'id="{i}"' not in html)
    assert not missing, f"app.js references ids that index.html lacks: {missing}"


def test_every_element_the_script_looks_up_by_id_exists_in_the_markup():
    html, app = _read("index.html"), _read("app.js")
    wanted = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', app))
    missing = sorted(i for i in wanted if f'id="{i}"' not in html)
    assert not missing, f"app.js references ids that index.html lacks: {missing}"


# ---- V81: every tray-menu control has a dashboard equivalent ------------------

TRAY = WEB.parent / "tray.ahk"


def _tray_submenu_items(builder: str) -> list[str]:
    src = TRAY.read_text(encoding="utf-8")
    body = re.search(rf"{builder}\(\) \{{(.*?)\n\}}", src, re.S).group(1)
    return re.findall(r'm\.Add\("([^"]+)"', body)


def test_quick_controls_cover_every_tray_quick_toggle():
    toggles = _tray_submenu_items("BuildTogglesMenu_Impl")
    groups = re.findall(r'data-qc="([a-z_]+)"', _read("index.html"))
    # One dashboard group per tray toggle: adding a tray toggle without a dashboard
    # control (or the reverse) fails here.
    assert len(toggles) == len(groups) == 5, (toggles, groups)
    assert set(groups) == {"performance", "tone", "history", "autostart", "clipboard_watcher"}
    app = _read("app.js")
    for group in groups:
        assert re.search(rf"\b{group}: \(v\) =>", app), f"no daemon action wired for {group}"


def test_server_and_app_controls_cover_the_tray_server_menu_diagnostics_and_exit():
    server = _tray_submenu_items("BuildServerMenu_Impl")
    assert {"Warmup", "Stop", "Check for updates…"} <= set(server)
    html, app = _read("index.html"), _read("app.js")
    prefixes = json.loads(re.search(r"const SERVER_APP_PREFIXES = (\[.*?\]);", app).group(1))
    assert prefixes == ["sa", "cfg-app"]          # Overview card + Config › App
    for p in prefixes:
        for suffix in ("server", "version", "warmup", "stop", "update-check", "update-apply",
                       "diagnostics", "exit", "status"):
            assert f'id="{p}-{suffix}"' in html, f"{p}-{suffix} missing"
        assert re.search(rf'id="{p}-update-apply"[^>]*\shidden', html)  # only once an update exists


def test_config_essentials_carries_every_tray_quick_toggle():
    html = _read("index.html")
    essentials = "".join(
        m.group(0) for m in re.finditer(
            r'<div class="card[^"]*" id="config-[a-z-]+"\s+data-config-section="essentials".*?\n      </div>', html, re.S)
    )
    for marker in ('name="perf"', 'name="tone"', 'id="cfg-store-text"', 'id="cfg-autostart"', 'id="cfg-clipwatch"'):
        assert marker in essentials, f"{marker} is not in Config › Essentials"


def test_every_config_section_tab_has_cards_and_a_title():
    html, app = _read("index.html"), _read("app.js")
    tabs = re.findall(r'role="tab" class="config-section-tab[^"]*"\s+data-config-section="([a-z]+)"', html)
    meta = re.search(r"const CONFIG_SECTION_META = \{(.*?)\n\};", app, re.S).group(1)
    for section in tabs:
        assert re.search(rf"^\s+{section}: \[", meta, re.M), f"no title/description for {section}"
        assert re.search(rf'<div class="card[^"]*" id="[^"]+"\s+data-config-section="{section}"', html), \
            f"section {section} has no cards"
    assert "app" in tabs


def test_ids_are_unique():
    ids = re.findall(r'\sid="([^"]+)"', _read("index.html"))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate ids (getElementById returns only the first): {dupes}"


def test_save_all_never_resends_the_instant_settings():
    # Saving the Config form must not carry its (possibly stale) copy of a setting
    # the tray or Quick controls change instantly -- that silently undid tray changes.
    app = _read("app.js")
    save = re.search(r"async function saveConfig\(\) \{(.*?)\n\}", app, re.S).group(1)
    for leaked in ("performance_mode", "history_store_text", "preset:", "set_autostart"):
        assert leaked not in save, f"saveConfig still sends {leaked}"


def test_meeting_model_temperature_and_redo_controls_are_present():
    html = _read("index.html")
    for element_id in ("mtg-model", "mtg-model-hint", "mtg-temp", "mtg-coverage", "mtg-redo-cut"):
        assert f'id="{element_id}"' in html
    # The redo button must start hidden: it is only meaningful when digests are cut short.
    assert re.search(r'id="mtg-redo-cut"[^>]*\shidden', html)
