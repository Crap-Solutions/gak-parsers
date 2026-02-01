"""API operations for GAK ticket tracking."""

import requests
import logging

REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


def fetch_events(base_url, events_ep, timeout=REQUEST_TIMEOUT):
    """Fetch events from API with error handling."""
    try:
        response = requests.get(base_url + events_ep, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            logger.error(f"Expected list from events API, got {type(data)}")
            return []

        return data
    except requests.exceptions.Timeout:
        logger.error("Timeout fetching events from API")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch events: {e}")
        return []
    except ValueError as e:
        logger.error(f"Invalid JSON response: {e}")
        return []


def fetch_event_details(base_url, event_id, timeout=REQUEST_TIMEOUT):
    """Fetch event details from API with error handling."""
    try:
        event_url = base_url + event_id + "/"
        chk_url = event_url + "public-stadium-representation-config"
        response = requests.get(chk_url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching details for event {event_id}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch details for event {event_id}: {e}")
        return None
    except ValueError as e:
        logger.error(f"Invalid JSON response for event {event_id}: {e}")
        return None


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
