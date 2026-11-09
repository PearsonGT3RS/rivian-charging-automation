import logging
import json
import asyncio
import time

try:
    import aiohttp
    from tesla_fleet_api import TeslaFleetApi
    from tesla_fleet_api.exceptions import VehicleOffline, OAuthExpired
    from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    _TESLA_DEPS_AVAILABLE = True
    _TESLA_DEPS_ERROR = None
except ImportError as _e:  # pragma: no cover - exercised only when deps missing
    aiohttp = None
    TeslaFleetApi = None
    VehicleSigned = None
    serialization = None
    default_backend = None

    class VehicleOffline(Exception):
        pass

    class OAuthExpired(Exception):
        pass

    _TESLA_DEPS_AVAILABLE = False
    _TESLA_DEPS_ERROR = _e

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5
    AMPS_MAX = 48
    
    def __init__(self, config_file="/app/config.json", session_file="/sessions/tesla-session.json"):
        if not _TESLA_DEPS_AVAILABLE:
            raise RuntimeError(
                'TeslaAPI requires the tesla-fleet-api and cryptography packages. '
                'Install them with: pip install -r requirements.txt'
            ) from _TESLA_DEPS_ERROR
        self.config_file = config_file
        self.session_file = session_file
        self.private_key_path = "/app/private-key.pem"
        
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.vehicle_id = None 
        self.last_known_soc = 0  # <--- NEW: Initialize cache
        self.last_known_limit = 0 # <--- NEW: Cache for current charge limit to avoid unnecessary API calls

        # --- NEW: Execution-level cache ---
        self._cached_vehicle_data = None
        self._cache_populated = False # Used to safely cache 'None' when asleep

        self._load_config()
        self.load_session()
        
    def _load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                config_data = json.load(f)
                self.client_id = config_data.get("tesla_client_id")
                self.client_secret = config_data.get("tesla_client_secret")
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")

    def load_session(self):
        try:
            with open(self.session_file) as f:
                session = json.load(f)
                self.access_token = session.get('access_token')
                self.refresh_token = session.get('refresh_token')
                self.last_known_soc = session.get("last_known_soc", 0) # <--- NEW: Load last known SoC from session for cache priming
                self.vehicle_id = session.get('vehicle_id') 
                # --- NEW: Load the limit from disk ---
                self.last_known_limit = session.get("last_known_limit", 80)
            
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("No valid session file found")
            self.last_known_soc = 0
            self.last_known_limit = 80

    def save_session(self):
        """Persist tokens to disk to maintain the single-use OAuth chain."""
        try:
            session_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "last_known_soc": self.last_known_soc,  # <--- NEW: Save last known SoC to session for cache priming
                "vehicle_id": self.vehicle_id,
                "last_known_limit": self.last_known_limit, # <--- NEW: Save the limit to disk
                "updated_at": int(time.time())
            }
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=4)
            logger.info("Tesla tokens updated and saved to disk.")
        except Exception as e:
            logger.error(f"Failed to save Tesla session: {e}")

    def force_refresh_tokens(self):
        """Manually refresh the OAuth tokens. Returns True on success.

        Use this if the automation has been idle long enough that the access
        token expired. If this returns False, the refresh token itself is
        expired/revoked and you must re-run the Tesla OAuth flow.
        """
        async def _refresh():
            async with aiohttp.ClientSession() as session:
                return await self._refresh_tokens_async(session)
        return asyncio.run(_refresh())

    def _get_parsed_key(self):
        try:
            with open(self.private_key_path, "rb") as key_file:
                return serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Key parsing failed: {e}")
            return None

    def _run_async(self, coro_func, *args, **kwargs):
        """Bridge between sync main loop and async library."""
        async def wrapper():
            async with aiohttp.ClientSession() as session:
                if not self.access_token:
                    if not await self._refresh_tokens_async(session): return None

                parsed_key = self._get_parsed_key()
                if not parsed_key: return None

                api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                api.private_key = parsed_key
                vehicle = VehicleSigned(api, self.vehicle_id) if self.vehicle_id else None

                try:
                    return await coro_func(api, vehicle, *args, **kwargs)

                except BaseException as e:
                    # OAuthExpired does NOT inherit from Exception in tesla-fleet-api,
                    # and duplicate module instances can make isinstance() unreliable.
                    # Match by class name + message instead.
                    if type(e).__name__ == 'VehicleOffline':
                        logger.debug("Tesla is asleep. Safely returning None.")
                        return None

                    is_oauth_expired = (
                        type(e).__name__ == 'OAuthExpired'
                        or 'oauth token has expired' in str(e).lower()
                    )
                    if is_oauth_expired:
                        logger.info(f"Tesla OAuth token expired ({e}). Attempting refresh...")
                        if not await self._refresh_tokens_async(session):
                            logger.error(
                                "Tesla token refresh failed. The refresh token is likely "
                                "expired or revoked. Re-run the Tesla OAuth flow to obtain "
                                "new tokens and update the session file."
                            )
                            return None
                        return await self._retry_after_refresh(
                            session, parsed_key, coro_func, *args, **kwargs
                        )

                    # Fallback for auth errors surfaced as generic exceptions
                    is_auth_error = "401" in str(e).lower() or "unauthorized" in str(e).lower()
                    if is_auth_error:
                        logger.info("Tesla token expired (string match). Attempting refresh...")
                        if not await self._refresh_tokens_async(session):
                            logger.error(
                                "Tesla token refresh failed. Re-authentication required."
                            )
                            return None
                        return await self._retry_after_refresh(
                            session, parsed_key, coro_func, *args, **kwargs
                        )

                    logger.error(f"Tesla Request Failed: {e}")
                    return None
                    
        return asyncio.run(wrapper())

    async def _retry_after_refresh(self, session, parsed_key, coro_func, *args, **kwargs):
        """Rebuild the API client with fresh tokens and retry the coroutine once."""
        api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
        api.private_key = parsed_key
        vehicle = VehicleSigned(api, self.vehicle_id) if self.vehicle_id else None
        try:
            return await coro_func(api, vehicle, *args, **kwargs)
        except BaseException as retry_e:
            if type(retry_e).__name__ == 'VehicleOffline':
                logger.debug("Tesla is asleep after token refresh. Safely returning None.")
                return None
            if (type(retry_e).__name__ == 'OAuthExpired'
                    or 'oauth token has expired' in str(retry_e).lower()):
                logger.error(
                    f"Tesla OAuth still expired after refresh ({retry_e}). "
                    "Re-authentication required."
                )
                return None
            logger.error(f"Retry failed after token refresh: {retry_e}")
            return None

    async def _refresh_tokens_async(self, session):
        url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        try:
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        f"Tesla token refresh failed: HTTP {resp.status} - {body}"
                    )
                    return False
                tokens = await resp.json()
                if not tokens:
                    logger.error("Tesla token refresh returned empty payload")
                    return False

                self.access_token = tokens["access_token"]
                self.refresh_token = tokens["refresh_token"]

                if not self.access_token:
                    logger.error("Tesla token refresh returned no access_token")
                    return False

                self.save_session()
                logger.info("Tesla tokens refreshed and saved.")
                return True
        except Exception as e:
            logger.error(f"Tesla token refresh raised: {e}")
            return False

    # --- Commands (Synchronous Public Methods) ---

    def is_charger_connected(self):
        data = self.get_vehicle_data()
        if not data:
            return True
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state != 'Disconnected'

    def wake_up(self):
        """Wakes the vehicle safely, skipping the delay if already awake."""
        if self.get_battery_level() > 0:
            logger.info("Tesla is already awake and talking. Skipping wake delay.")
            return True

        logger.info("Sending Tesla wake command...")
        
        async def _wake(api, vehicle):
            return await vehicle.wake_up()
            
        result = self._run_async(_wake)
        
        logger.info("Waiting 30 seconds for Highland telemetry to boot...")
        time.sleep(30)
        
        # --- NEW: Cache Invalidation ---
        # The car is now awake. Flush the 'None' cache so the next call 
        # hits the network to get the fresh telemetry payload.
        self._cached_vehicle_data = None
        self._cache_populated = False

        return result

    def get_vehicle_data(self, force_refresh=False):
        """Fetches data from the API or serves it from the ephemeral cache."""
        if not force_refresh and self._cache_populated:
            return self._cached_vehicle_data
        
        async def _get(api, vehicle): return await vehicle.vehicle_data()

        self._cached_vehicle_data = self._run_async(_get)
        self._cache_populated = True
        
        return self._cached_vehicle_data

    def get_battery_level(self):
        data = self.get_vehicle_data()
        if not data:
            return 0 
            
        # Safely extract the SOC using your exact path
        soc = data.get('response', {}).get('charge_state', {}).get('battery_level', 0)
        limit = data.get('response', {}).get('charge_state', {}).get('charge_limit_soc', 100)

        # Save to persistent disk only if we got a valid reading and it changed
        if soc > 0 and (self.last_known_soc != soc or getattr(self, 'last_known_limit', 100) != limit):
            self.last_known_soc = soc
            self.last_known_limit = limit
            self.save_session()
            
        return soc

    # --- NEW: Limit Getter Methods ---
    def get_vehicle_limit(self):
        """Returns the internal charge limit set on the vehicle's screen."""
        data = self.get_vehicle_data()
        if not data:
            return getattr(self, 'last_known_limit', 100)
        return data.get('response', {}).get('charge_state', {}).get('charge_limit_soc', 100)

    def get_cached_vehicle_limit(self):
        """Returns the persistent limit without waking the car."""
        return getattr(self, 'last_known_limit', 100)


    def get_cached_battery_level(self):
        """Returns the persistent SOC without waking the car."""
        return self.last_known_soc

    def is_charging(self):
        data = self.get_vehicle_data()
        if not data:
            return False
        state = data.get('response', {}).get('charge_state', {}).get('charging_state')
        return state == 'Charging'

    def charge_start(self):
        async def _start(api, vehicle): return await vehicle.charge_start()
        return self._run_async(_start)

    def charge_stop(self):
        async def _stop(api, vehicle): return await vehicle.charge_stop()
        return self._run_async(_stop)

    def set_charging_amps(self, amps):
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        async def _set(api, vehicle): return await vehicle.set_charging_amps(amps)
        return self._run_async(_set)

    def get_current_schedule_amp(self):
        data = self.get_vehicle_data()
        if not data:
            return 0
        return data.get('response', {}).get('charge_state', {}).get('charge_amps', 0)

    # --- Compatibility Aliases ---
    def set_schedule_amps(self, amps): return self.set_charging_amps(amps)
    def set_schedule_off(self): return self.charge_stop()
    def set_schedule_default(self): return self.set_schedule_amps(self.AMPS_MAX)
