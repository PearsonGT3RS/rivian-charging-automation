import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)


@dataclass
class PowerFlow:
    pv: float    # PV production in watts
    grid: float  # Grid power flow in watts (positive = import, negative = export)
    load: float  # Load consumption in watts


class LoadUnavailableError(RuntimeError):
    """Raised when a load_provider was supplied but could not return a value."""


class SolarEdgeAPI:
    """
    SolarEdge v2 API client (OAuth 2.0 Bearer token).

    Notes on v2:
      - Access tokens expire in 2 hours.
      - Refresh tokens expire after 14 days of non-use and ROTATE on every
        refresh. The newest refresh token must always be persisted.
      - The Free tier does not expose a live power-flow endpoint. We approximate
        "current PV power" from the most recent 15-minute sample of
        /sites/{id}/power. Load is not available, so we assume load == pv
        (grid == 0) and log a warning. This preserves the PowerFlow interface
        used by ChargingAutomation.py.
    """

    BASE_URL = "https://monitoringapi.solaredge.com/v2"
    TOKEN_URL = f"{BASE_URL}/oauth2/token"
    DEFAULT_TOKEN_FILE = "/sessions/solaredge-token.json"
    # Refresh proactively if the access token has less than this many seconds left.
    REFRESH_SKEW_SECONDS = 300
    # Refresh tokens expire after 14 days of non-use. We refresh every 6 days
    # to keep the token chain alive with a comfortable margin, regardless of
    # whether the SolarEdge data endpoint is being used (HA PV may be the
    # primary source).
    KEEPALIVE_INTERVAL_SECONDS = 6 * 24 * 60 * 60

    def __init__(self, credentials_file, token_file=None):
        """Initialize with credentials file path and (optional) token cache path."""
        with open(credentials_file) as f:
            credentials = json.load(f)

        self.client_id = credentials['solaredge-client-id']
        self.client_secret = credentials['solaredge-client-secret']
        self.site_id = credentials['solaredge-site-id']
        self.redirect_uri = credentials.get(
            'solaredge-redirect-uri', 'http://localhost:8080/oauth/callback'
        )
        self.token_file = token_file or self.DEFAULT_TOKEN_FILE

        self._access_token = None
        self._refresh_token = None
        self._access_expires_at = 0.0  # epoch seconds

        self._load_token_file()

    # ------------------------------------------------------------------ tokens

    def _load_token_file(self):
        if not os.path.exists(self.token_file):
            logger.warning(
                'No SolarEdge token file at %s. Run the OAuth bootstrap first: '
                'python SolarEdgeAPI.py <authorization_code>',
                self.token_file,
            )
            return
        try:
            with open(self.token_file) as f:
                data = json.load(f)
            self._access_token = data.get('access_token')
            self._refresh_token = data.get('refresh_token')
            self._access_expires_at = float(data.get('access_expires_at', 0))
        except (OSError, ValueError) as e:
            logger.error('Failed to read SolarEdge token file: %s', e)

    def _save_token_file(self):
        os.makedirs(os.path.dirname(self.token_file) or '.', exist_ok=True)
        tmp = f'{self.token_file}.tmp'
        with open(tmp, 'w') as f:
            json.dump({
                'access_token': self._access_token,
                'refresh_token': self._refresh_token,
                'access_expires_at': self._access_expires_at,
            }, f)
        os.replace(tmp, self.token_file)

    def _store_token_response(self, payload):
        self._access_token = payload['access_token']
        # v2 rotates the refresh token; always keep the newest one.
        if payload.get('refresh_token'):
            self._refresh_token = payload['refresh_token']
        expires_in = int(payload.get('expires_in', 7200))
        self._access_expires_at = time.time() + expires_in
        self._save_token_file()

    def _token_is_fresh(self):
        return (
            self._access_token is not None
            and time.time() < (self._access_expires_at - self.REFRESH_SKEW_SECONDS)
        )

    def _refresh_access_token(self):
        if not self._refresh_token:
            raise RuntimeError(
                'No SolarEdge refresh token available. Re-run the OAuth bootstrap.'
            )
        logger.info('Refreshing SolarEdge access token...')
        # SolarEdge v2 OAuth token endpoint expects application/x-www-form-urlencoded
        # per RFC 6749. Sending JSON causes the server to return HTTP 500.
        resp = requests.post(self.TOKEN_URL, data={
            'grant_type': 'refresh_token',
            'refresh_token': self._refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }, timeout=30)
        if resp.status_code != 200:
            # SolarEdge returns HTTP 500 (not 400) when the refresh token is
            # expired or revoked. Surface the body so the cause is obvious.
            body = resp.text
            logger.error(
                'SolarEdge token refresh failed: HTTP %d - %s',
                resp.status_code, body,
            )
            if 'invalid_grant' in body or resp.status_code == 500:
                logger.error(
                    'The SolarEdge refresh token is expired or revoked. '
                    'Re-run the OAuth bootstrap: '
                    'python SolarEdgeAPI.py bootstrap <credentials.json> <authorization_code>'
                )
            resp.raise_for_status()
        self._store_token_response(resp.json())

    def _ensure_token(self):
        if not self._token_is_fresh():
            self._refresh_access_token()

    def _maybe_keepalive_refresh(self):
        """
        Refresh the token only if it hasn't been refreshed in a while.

        Used during night hours when we don't need live data but must keep the
        refresh token from expiring (v2 invalidates refresh tokens after 14
        days of non-use). No-op if the token file is missing or already fresh
        enough that a normal daytime cycle would refresh it anyway.
        """
        if not self._refresh_token:
            return
        age = time.time() - self._access_expires_at
        # _access_expires_at is the *expiry* time, so a token refreshed N
        # seconds ago has age = N - expires_in. Compare against the keepalive
        # interval plus the token lifetime to get "time since last refresh".
        if age < (self.KEEPALIVE_INTERVAL_SECONDS - 7200):
            return
        logger.info('SolarEdge keepalive: refreshing token to prevent 14-day expiry')
        try:
            self._refresh_access_token()
        except Exception as e:
            logger.warning('SolarEdge keepalive refresh failed: %s', e)

    def _auth_headers(self):
        self._ensure_token()
        return {'Authorization': f'Bearer {self._access_token}'}

    # --------------------------------------------------------------- bootstrap

    @classmethod
    def bootstrap_from_code(cls, credentials_file, code, token_file=None):
        """
        One-time OAuth code exchange. Run after completing the browser authorize
        flow. Persists the resulting token pair to token_file.
        """
        with open(credentials_file) as f:
            credentials = json.load(f)

        # SolarEdge v2 OAuth token endpoint expects application/x-www-form-urlencoded
        # per RFC 6749. Sending JSON causes the server to return HTTP 500.
        resp = requests.post(cls.TOKEN_URL, data={
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': credentials['solaredge-client-id'],
            'client_secret': credentials['solaredge-client-secret'],
            'redirect_uri': credentials.get(
                'solaredge-redirect-uri', 'http://localhost:8080/oauth/callback'
            ),
        }, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        api = cls.__new__(cls)
        api.client_id = credentials['solaredge-client-id']
        api.client_secret = credentials['solaredge-client-secret']
        api.site_id = credentials['solaredge-site-id']
        api.redirect_uri = credentials.get(
            'solaredge-redirect-uri', 'http://localhost:8080/oauth/callback'
        )
        api.token_file = token_file or cls.DEFAULT_TOKEN_FILE
        api._access_token = None
        api._refresh_token = None
        api._access_expires_at = 0.0
        api._store_token_response(payload)
        logger.info('SolarEdge token saved to %s', api.token_file)
        return api

    # ------------------------------------------------------------------- data

    def _get(self, path, params=None):
        url = f'{self.BASE_URL}{path}'
        resp = requests.get(url, headers=self._auth_headers(), params=params, timeout=30)
        if resp.status_code == 401:
            # Token may have been revoked; force a refresh and retry once.
            logger.warning('SolarEdge 401; forcing token refresh and retrying')
            self._access_expires_at = 0
            resp = requests.get(url, headers=self._auth_headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_current_power(self):
        """Get current PV production in watts (latest 15-min sample)."""
        now = datetime.now(timezone.utc)
        # v2 requires ISO-8601 Instants (with Z / offset), not bare local times.
        # Look back 60 minutes so a single delayed/null bucket doesn't force us
        # to fall back to a stale or zero sample.
        frm = (now - timedelta(minutes=60)).strftime('%Y-%m-%dT%H:%M:%SZ')
        to = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            data = self._get(f'/sites/{self.site_id}/power', params={
                'from': frm,
                'to': to,
                'resolution': 'QUARTER_HOUR',
                'unit': 'W',
            })
            values = data.get('values') or []
            # The most recent bucket can be null while the inverter is still
            # reporting. Walk backwards to the newest non-null sample.
            for sample in reversed(values):
                value = sample.get('value')
                if value is not None:
                    return float(value)
            return None
        except requests.exceptions.HTTPError as e:
            # Distinguish auth failures (token refresh failed) from transient
            # data errors so the operator knows whether to re-bootstrap.
            if e.response is not None and e.response.status_code in (401, 403):
                logger.error(
                    'SolarEdge auth failed (%d). The refresh token is likely '
                    'expired; re-run the OAuth bootstrap.',
                    e.response.status_code,
                )
            else:
                logger.error('Error getting current power: %s', e)
            return None
        except requests.exceptions.RequestException as e:
            logger.error('Error getting current power: %s', e)
            return None

    def get_power_details(self):
        """Return (load_watts, grid_watts) from the v2 meter telemetry endpoint.

        v2 /powerDetails exposes per-meter breakdowns:
          - CONSUMPTION: site load (requires a consumption meter)
          - FEED_IN:     grid export (positive = export)
          - PRODUCTION:  PV production
          - SELF_CONSUMPTION: PV consumed on-site

        Returns (load, grid) in watts with our PowerFlow convention
        (grid positive = import). Either value may be None if the
        corresponding meter is not configured or has no recent sample.
        """
        now = datetime.now(timezone.utc)
        frm = (now - timedelta(minutes=60)).strftime('%Y-%m-%dT%H:%M:%SZ')
        to = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            data = self._get(f'/sites/{self.site_id}/meters/telemetry', params={
                'from': frm,
                'to': to,
                'resolution': 'QUARTER_HOUR',
            })
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                logger.error(
                    'SolarEdge meter telemetry auth failed (%d). The refresh '
                    'token is likely expired; re-run the OAuth bootstrap.',
                    e.response.status_code,
                )
            else:
                logger.error('Error getting meter telemetry: %s', e)
            return None, None
        except requests.exceptions.RequestException as e:
            logger.error('Error getting meter telemetry: %s', e)
            return None, None

        def _latest(metric):
            # v2 returns each metric as a list of {date, value} samples.
            for sample in reversed(data.get(metric) or []):
                value = sample.get('value')
                if value is not None:
                    return float(value)
            return None

        load = _latest('consumptionPower')
        import_power = _latest('importPower')
        export_power = _latest('exportPower')
        # Prefer the explicit import/export meters when present; otherwise
        # derive grid from load - pv at the call site.
        if import_power is not None:
            grid = import_power
        elif export_power is not None:
            grid = -export_power
        else:
            grid = None
        return load, grid

    def get_current_power_flow(self, load_provider=None, pv_override=None):
        """
        Compute power flow from v2 PV data plus an external load source.

        v2 Free tier does not expose load or grid directly. If load_provider is
        supplied (a callable returning load in watts), we compute
        grid = load - pv. If the provider returns None, LoadUnavailableError is
        raised so the caller can abort rather than charge blind. If no provider
        is supplied, we fall back to assuming load == pv (grid == 0).

        If pv_override is supplied (a numeric PV value in watts), it is used
        instead of calling the SolarEdge /power endpoint. This lets callers
        prefer an alternate PV source (e.g. Home Assistant / Modbus) while
        still reusing the PowerFlow construction and logging here.
        """
        if pv_override is not None:
            pv = float(pv_override)
        else:
            pv = self.get_current_power()
            if pv is None:
                return None

        if load_provider is not None:
            load = load_provider()
            if load is None:
                raise LoadUnavailableError(
                    'load_provider returned None; cannot compute grid'
                )
            grid = load - pv
        else:
            # No external load provider (e.g. Home Assistant is down). Try the
            # SolarEdge meter telemetry endpoint, which exposes consumption and
            # import/export meters when the site has them installed.
            se_load, se_grid = self.get_power_details()
            if se_load is not None:
                load = se_load
                # Prefer the import/export-derived grid when available (it
                # accounts for battery/storage flows); otherwise derive from
                # load - pv.
                grid = se_grid if se_grid is not None else (load - pv)
                logger.info(
                    'SolarEdge v2: using meter telemetry for load/grid '
                    '(load=%.0fW grid=%.0fW)', load, grid,
                )
            else:
                logger.warning(
                    'SolarEdge v2: no load_provider and no consumption meter; '
                    'assuming load == pv (grid == 0)'
                )
                load = pv
                grid = 0.0
        logger.info(
            'Power flow: pv=%.0fW load=%.0fW grid=%.0fW (positive grid = import)',
            pv, load, grid,
        )
        return PowerFlow(pv=pv, grid=grid, load=load)


def print_current_energy_production(credentials_file):
    """Print current energy production from SolarEdge inverter."""
    api = SolarEdgeAPI(credentials_file)
    power = api.get_current_power()
    if power is not None:
        print(f"Current power production: {power} W")
    else:
        print("Could not retrieve current power production")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 3:
        print('Usage:')
        print('  python SolarEdgeAPI.py bootstrap <credentials.json> <authorization_code>')
        print('  python SolarEdgeAPI.py test <credentials.json>')
        sys.exit(1)
    cmd = sys.argv[1]
    creds = sys.argv[2]
    if cmd == 'bootstrap':
        if len(sys.argv) < 4:
            print('Missing authorization code')
            sys.exit(1)
        SolarEdgeAPI.bootstrap_from_code(creds, sys.argv[3])
    elif cmd == 'test':
        print_current_energy_production(creds)
    else:
        print(f'Unknown command: {cmd}')
        sys.exit(1)
