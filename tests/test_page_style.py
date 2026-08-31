"""Tests pinning the ticket page's layout CSS.

2026-08 layout review: the season-summary box sat flush (0px) against the
past-events grid below it. Root cause: the grid's `gap` only separates cells
*inside* the grid, and `.season-summary` declared a top margin but no bottom
margin, while sibling margins above collapsed to 15px -- so the spacing was
asymmetric (15px above, 0px below). Related findings fixed in the same pass:
a duplicated `.past-event-card .mini-graph` rule left over from a reorganize,
mini graphs force-squeezed into a fixed 60px band (stretching the tight-cropped
~194x100 PNGs to ~2x their aspect), no viewport meta (mobile browsers render a
~980px virtual viewport, so the grid's 768/480px breakpoints never fire), and a
main graph capped at max-width only (overflowing below ~1040px viewports).

These tests render the real template so a regression in any of these reappears
as a red suite instead of a silently squashed page.
"""
import re
from pathlib import Path

import jinja2

TEMPLATE = Path(__file__).resolve().parent.parent / "tickets" / "templates" / "ticket-html.tmpl"


def _render():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)))
    return env.get_template(TEMPLATE.name).render(
        events=[{"title": "X : Y", "sold": 1, "avail": 0}],
        img="", past_events=[], season_summary="avg",
        last_updated="", last_updated_iso="2026-01-01T00:00:00Z",
        EST_SEASON_TICKETS=0, EST_SPONSORS=0, EST_VIP=0,
        EST_EXTRA=0, EST_DEDUCT=0,
    )


def _css_rules(page):
    """Yield (selector, declarations) for every rule in the page's <style>."""
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    for m in re.finditer(r"([.#\w\-\[\]=\", ()]+?)\s*\{([^}]*)\}", css):
        yield m.group(1).strip(), m.group(2)


def test_season_summary_has_bottom_margin():
    # The reported bug: summary had margin-top but no margin-bottom, so it sat
    # flush against the grid below (grid `gap` does not apply outside the grid).
    rules = dict(_css_rules(_render()))
    decls = rules[".season-summary"]
    m = re.search(r"margin-bottom:\s*([\d.]+)px", decls)
    assert m and float(m.group(1)) > 0, decls


def test_no_duplicate_css_rules():
    # A duplicated rule half-overrides its twin and breeds drift (the old
    # `.past-event-card .mini-graph` pair disagreed on margin-top/flex-shrink).
    selectors = [sel for sel, _ in _css_rules(_render())]
    dupes = {s for s in selectors if selectors.count(s) > 1}
    assert not dupes, f"duplicate CSS rules: {dupes}"


def test_mini_graph_height_is_auto():
    # Mini-graph PNGs are tight-cropped (aspect varies per event), so a fixed
    # height over width:100% stretches them. height:auto preserves the aspect.
    rules = dict(_css_rules(_render()))
    assert re.search(r"height:\s*auto", rules[".past-event-card .mini-graph"])


def test_mobile_viewport_and_responsive_images():
    # Without the viewport meta, phones render ~980px virtual and the grid's
    # 768/480px breakpoints never fire; without width:100% the 1000px main
    # graph overflows narrow viewports.
    page = _render()
    assert 'name="viewport"' in page
    rules = dict(_css_rules(page))
    assert re.search(r"width:\s*100%", rules[".main-graph"])
    assert re.search(r"<img class=\"main-graph\"[^>]*alt=", page)
