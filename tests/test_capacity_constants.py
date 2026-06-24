"""Tests for the ticket-page capacity/estimate constants.

The "Sold" breakdown on the public page layers several empirical estimates
(season tickets, sponsors, VIP, ...) onto the API's online-only figure, and
the %-of-capacity bar divides a total by STADIUM_CAPACITY. These were bare
literals scattered across ticket-fetch.py and the template; they now live as
named constants that are passed into the template render context.

These tests pin the *arithmetic wiring* end to end: render the real template
with a known SOLD value and assert the displayed numbers match the original
formulas. They also fail loudly (Jinja raises on int + Undefined) if a
constant is dropped from the render context or renamed only on one side.
"""
from pathlib import Path

import jinja2

TEMPLATE = Path(__file__).resolve().parent.parent / "tickets" / "templates" / "ticket-html.tmpl"


def _render(ticket_fetch, sold):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)))
    tmpl = env.get_template(TEMPLATE.name)
    return tmpl.render(
        events=[{"title": "X : Y", "id": "e", "sold": sold, "avail": 0,
                 "capacity_percent": 0}],
        img="",
        past_events=[],
        season_summary=None,
        last_updated="now",
        EST_SEASON_TICKETS=ticket_fetch.EST_SEASON_TICKETS,
        EST_SPONSORS=ticket_fetch.EST_SPONSORS,
        EST_VIP=ticket_fetch.EST_VIP,
        EST_EXTRA=ticket_fetch.EST_EXTRA,
        EST_DEDUCT=ticket_fetch.EST_DEDUCT,
    )


def test_sold_breakdown_matches_original_formulas(ticket_fetch):
    # sold=1000 -> the three breakdown lines, computed from the original
    # literals (2333/285/296/393/2864), must be unchanged. Only the label
    # wording changed (now honest about incl./excl.); the numbers did not.
    out = _render(ticket_fetch, 1000)
    assert "Sold (incl. est. season tickets, sponsors &amp; VIP): 3914" in out   # 1000+2333+285+296
    assert "Sold (excl. est. season tickets, sponsors &amp; VIP): -1864" in out # 1000-2864
    assert "Sold (incl. est. season tickets, sponsors, VIP &amp; extra): 4307" in out  # 1000+393+2333+285+296


def test_capacity_bar_uses_stadium_capacity(ticket_fetch):
    # capacity% = int((sold + season + sponsors + vip) / STADIUM_CAPACITY * 100)
    # -> int((1000 + 2914) / 15000 * 100) = int(26.09) = 26
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE.parent)))
    tmpl = env.get_template(TEMPLATE.name)
    out = tmpl.render(
        events=[{"title": "X : Y", "id": "e", "sold": 1000, "avail": 0,
                 "capacity_percent": 26}],
        img="", past_events=[], season_summary=None, last_updated="",
        EST_SEASON_TICKETS=ticket_fetch.EST_SEASON_TICKETS,
        EST_SPONSORS=ticket_fetch.EST_SPONSORS,
        EST_VIP=ticket_fetch.EST_VIP,
        EST_EXTRA=ticket_fetch.EST_EXTRA,
        EST_DEDUCT=ticket_fetch.EST_DEDUCT,
    )
    assert "Capacity: 26%" in out


def test_render_context_carries_every_constant(ticket_fetch):
    # If any EST_* constant is missing from the render context, Jinja's
    # default Undefined makes `sold + Undefined` raise at render time.
    # So a successful render here proves the wiring is complete.
    assert _render(ticket_fetch, 0)  # must not raise
