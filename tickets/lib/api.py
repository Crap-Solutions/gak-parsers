"""API operations for GAK ticket tracking."""

import requests
import logging

REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when a fetch from the ticket API fails (network/HTTP/parse).

    The api functions raise rather than logging/alerting here, so the caller
    can decide how to surface the failure (e.g. the cron alert-grace window
    that suppresses emails during the upstream server's regular downtimes).
    """


def fetch_events(base_url, events_ep, timeout=REQUEST_TIMEOUT):
    """Fetch the list of future published events from the API.

    Returns the parsed list (possibly empty) on success. Raises FetchError
    on any network, HTTP, or parse failure, so a server problem can be told
    apart from a genuinely empty result.
    """
    try:
        response = requests.get(base_url + events_ep, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout as e:
        raise FetchError("Timeout fetching events from API") from e
    except requests.exceptions.RequestException as e:
        raise FetchError(f"Failed to fetch events: {e}") from e
    except ValueError as e:
        raise FetchError(f"Invalid JSON response: {e}") from e

    if not isinstance(data, list):
        raise FetchError(
            f"Expected list from events API, got {type(data).__name__}")

    return data


def fetch_event_details(base_url, event_id, timeout=REQUEST_TIMEOUT):
    """Fetch event details (stadium representation config) from the API.

    Returns the parsed JSON on success. Raises FetchError on any network,
    HTTP, or parse failure.
    """
    chk_url = base_url + event_id + "/public-stadium-representation-config"
    try:
        response = requests.get(chk_url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout as e:
        raise FetchError(f"Timeout fetching details for event {event_id}") from e
    except requests.exceptions.RequestException as e:
        raise FetchError(f"Failed to fetch details for event {event_id}: {e}") from e
    except ValueError as e:
        raise FetchError(f"Invalid JSON response for event {event_id}: {e}") from e


def parse_event_data(event, content):
    """Parse event data from API response with validation."""
    try:
        if not isinstance(content, dict):
            logger.error(f"Event {event.get('id')}: expected dict, got {type(content)}")
            return None

        if 'sectorRepresentationConfigurations' not in content:
            logger.error(f"Event {event.get('id')}: missing sectorRepresentationConfigurations")
            return None

        if not isinstance(content['sectorRepresentationConfigurations'], list):
            logger.error(f"Event {event.get('id')}: sectorRepresentationConfigurations is not a list")
            return None

        sold_cnt = 0
        avail_cnt = 0
        for entry in content['sectorRepresentationConfigurations']:
            if not isinstance(entry, dict):
                continue

            seat_configs = entry.get('seatConfigurations', [])
            if not isinstance(seat_configs, list):
                logger.warning(f"Event {event.get('id')}: invalid seatConfigurations, skipping")
                continue

            for e in seat_configs:
                if e.get('seatStatus') == 'SOLD':
                    sold_cnt += 1
                elif e.get('seatStatus') == 'AVAILABLE':
                    avail_cnt += 1

        return {
            "title": event.get("title", "Unknown"),
            "id": event.get("id", ""),
            "sold": sold_cnt,
            "avail": avail_cnt
        }
    except Exception as e:
        logger.error(f"Error parsing event data: {e}")
        return None
