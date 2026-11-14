import logging

import requests

logger = logging.getLogger(__name__)


class HomeAssistantUnavailable(RuntimeError):
    """Raised when Home Assistant itself is unreachable (connection error).

    Distinct from a sensor being 'unavailable'/'unknown', which is a normal
    condition and is reported as a None return value.
    """


class HomeAssistantAPI:
    """Minimal Home Assistant REST client for reading sensor states."""

    def __init__(self, base_url, token, timeout=10):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

    def is_available(self):
        """Return True if the HA API is reachable, False otherwise.

        Used to short-circuit a cycle when HA is down (e.g. during an upgrade)
        instead of making many failing per-sensor requests.
        """
        url = f'{self.base_url}/api/'
        try:
            requests.get(url, headers=self._headers(), timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            logger.warning('Home Assistant unreachable: %s', e)
            return False
        # Any HTTP response (even 401) means the host is up; we only care
        # about reachability here.
        return True

    def get_sensor_value(self, entity_id):
        """
        Return the numeric state of a sensor, or None if unavailable/unknown/error.

        Assumes the sensor reports watts (W). If the unit is kW, converts to W.
        """
        url = f'{self.base_url}/api/states/{entity_id}'
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as e:
            # HA itself is unreachable; let the caller decide whether to abort
            # the cycle rather than silently treating every sensor as missing.
            raise HomeAssistantUnavailable(str(e)) from e
        except requests.exceptions.RequestException as e:
            logger.error('HA sensor %s fetch failed: %s', entity_id, e)
            return None

        state = data.get('state')
        if state in (None, 'unknown', 'unavailable'):
            logger.warning('HA sensor %s state is %s', entity_id, state)
            return None

        try:
            value = float(state)
        except (TypeError, ValueError):
            logger.error('HA sensor %s non-numeric state: %r', entity_id, state)
            return None

        unit = (data.get('attributes') or {}).get('unit_of_measurement', 'W')
        if unit == 'kW':
            value *= 1000.0
        elif unit not in ('W', 'kW'):
            logger.warning('HA sensor %s unexpected unit %r; assuming W', entity_id, unit)

        return value

    def get_sensor_sum(self, entity_ids):
        """Return the sum of the given sensors, or None if any is unavailable."""
        total = 0.0
        for entity_id in entity_ids:
            value = self.get_sensor_value(entity_id)
            if value is None:
                return None
            total += value
        return total
