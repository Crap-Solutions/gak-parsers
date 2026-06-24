"""Tests for lib/corrections.py — the per-event SOLD corrections.

These thresholds are empirical patches for known upstream reporting bugs
(there is a TODO to remove them once the events age out). Pinning them means
removing a correction -- or a future refactor -- breaks loudly instead of
silently shifting the published numbers.

apply_ticket_corrections is NOT generally idempotent (some rules re-trigger
on a second pass), so every test applies it exactly once, matching real use.
"""
from lib import corrections

UNKNOWN = "00000000-0000-0000-0000-000000000000"


def test_unknown_event_unchanged():
    assert corrections.apply_ticket_corrections(UNKNOWN, 9999, 0.0) == 9999


def test_456e9a8a_subtracts_above_threshold():
    eid = "456e9a8a-ce64-4580-b9e0-3405a810c696"
    assert corrections.apply_ticket_corrections(eid, 2302, 0.0) == 2302 - 1939
    assert corrections.apply_ticket_corrections(eid, 5000, 0.0) == 5000 - 1939


def test_456e9a8a_unchanged_below_threshold():
    eid = "456e9a8a-ce64-4580-b9e0-3405a810c696"
    assert corrections.apply_ticket_corrections(eid, 2301, 0.0) == 2301


def test_aeee2d94_needs_both_sold_and_lead_time_conditions():
    eid = "aeee2d94-edae-4a6d-a65c-f2ae274361ef"
    # sold high AND far enough from KO -> subtract
    assert corrections.apply_ticket_corrections(eid, 5100, 74.21) == 5100 - 285
    # sold high but close to KO -> no change (rule is strictly > 74.20)
    assert corrections.apply_ticket_corrections(eid, 5100, 74.20) == 5100
    # far from KO but sold below threshold -> no change
    assert corrections.apply_ticket_corrections(eid, 5099, 100.0) == 5099


def test_2e9e16ba_is_cumulative_by_lead_time():
    eid = "2e9e16ba-e8c3-409e-8c41-d7e6ddfaab40"
    base = 1000
    # far from KO -> no allocation added
    assert corrections.apply_ticket_corrections(eid, base, 96.35) == base
    # inside 96.35h -> first allocation (296 + 285)
    assert corrections.apply_ticket_corrections(eid, base, 49.81) == base + 581
    # inside 49.8h  -> both allocations (581 + 2333)
    assert corrections.apply_ticket_corrections(eid, base, 49.79) == base + 581 + 2333


def test_h_diff_does_not_affect_other_events():
    # the 2e9e16ba lead-time rules must not fire for a different event
    assert corrections.apply_ticket_corrections(
        UNKNOWN, 1000, 1.0) == 1000
