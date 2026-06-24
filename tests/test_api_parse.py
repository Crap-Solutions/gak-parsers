"""Tests for lib/api.py parse_event_data — SOLD/AVAILABLE aggregation.

parse_event_data walks the nested sectorRepresentationConfigurations ->
seatConfigurations tree and counts seats by status. It is a pure function
and the heart of the data pipeline, but had no coverage. These tests pin the
documented validation paths and the aggregation.
"""
from lib import api


def _seat(status):
    return {"seatStatus": status}


def test_counts_sold_and_available_across_sectors():
    event = {"id": "e1", "title": "Match"}
    content = {"sectorRepresentationConfigurations": [
        {"seatConfigurations": [_seat("SOLD"), _seat("SOLD"), _seat("AVAILABLE")]},
        {"seatConfigurations": [_seat("AVAILABLE")]},
    ]}
    out = api.parse_event_data(event, content)
    assert out == {"title": "Match", "id": "e1", "sold": 2, "avail": 2}


def test_ignores_other_seat_statuses():
    event = {"id": "e1", "title": "M"}
    content = {"sectorRepresentationConfigurations": [
        {"seatConfigurations": [_seat("RESERVED"), _seat("SOLD"), {}]},
    ]}
    out = api.parse_event_data(event, content)
    assert out["sold"] == 1
    assert out["avail"] == 0


def test_missing_configurations_returns_none():
    assert api.parse_event_data({"id": "e1"}, {}) is None


def test_content_not_dict_returns_none():
    assert api.parse_event_data({"id": "e1"}, ["not", "a", "dict"]) is None


def test_configurations_not_list_returns_none():
    assert api.parse_event_data(
        {"id": "e1"}, {"sectorRepresentationConfigurations": {}}) is None


def test_seatconfigs_not_list_is_skipped():
    # a non-list seatConfigurations skips just that sector, not the event
    event = {"id": "e1", "title": "M"}
    content = {"sectorRepresentationConfigurations": [
        {"seatConfigurations": "nope"},
        {"seatConfigurations": [_seat("SOLD")]},
    ]}
    out = api.parse_event_data(event, content)
    assert out["sold"] == 1


def test_empty_configurations_returns_zeros():
    event = {"id": "e1", "title": "M"}
    content = {"sectorRepresentationConfigurations": []}
    out = api.parse_event_data(event, content)
    assert out == {"title": "M", "id": "e1", "sold": 0, "avail": 0}


def test_missing_event_fields_use_defaults():
    content = {"sectorRepresentationConfigurations": []}
    out = api.parse_event_data({}, content)
    assert out["id"] == ""
    assert out["title"] == "Unknown"
