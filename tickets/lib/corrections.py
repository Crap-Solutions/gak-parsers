"""Per-event data corrections for known upstream ticket-reporting issues.

The ticket API has historically misreported sales for a few specific events
(e.g. a bulk allocation appearing or disappearing at a known lead time),
which puts a discontinuity in the sales curve. These corrections patch the
recorded SOLD count as a function of how many hours before kick-off the
sample was taken (``h_diff``), so the curve and the final tally are
consistent.

This is the single source of truth: both the page renderer
(``ticket-fetch.py``) and the chart generator (``lib/graph.py``) call
``apply_ticket_corrections`` so the two can never drift apart again.

TODO: remove these once the upstream data is clean / the events age out.
"""

# Corrections are expressed as plain conditionals (not a rule DSL) on
# purpose: the thresholds are empirical and reading them as code is clearer
# than decoding a data structure.


def apply_ticket_corrections(event_id, tickets_sold, h_diff):
    """Return the corrected SOLD count for a single event sample.

    ``h_diff`` is hours until kick-off (may be negative for samples taken
    after KO). Unknown events are returned unchanged. Not idempotent in
    general (some rules re-trigger on a second pass), so apply exactly once
    per sample.
    """
    if event_id == "456e9a8a-ce64-4580-b9e0-3405a810c696":
        if tickets_sold >= 2302:
            tickets_sold = tickets_sold - 1939
    if event_id == "aeee2d94-edae-4a6d-a65c-f2ae274361ef":
        if tickets_sold >= 5100 and h_diff > 74.20:
            tickets_sold = tickets_sold - 285
    if event_id == "2e9e16ba-e8c3-409e-8c41-d7e6ddfaab40":
        if h_diff < 96.35:
            tickets_sold = tickets_sold + 296 + 285
        if h_diff < 49.8:
            tickets_sold = tickets_sold + 2333
    return tickets_sold
