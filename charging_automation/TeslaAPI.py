import logging
import json
import asyncio
import aiohttp
import time
from tesla_fleet_api import TeslaFleetApi
from tesla_fleet_api.exceptions import VehicleOffline, OAuthExpired
from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5
    AMPS_MAX = 48
    
    def __init__(self, config_file="/app/config.json", session_file="/sessions/tesla-session.json"):
        self.config_file = config_file
        self.session_file = session_file
        self.private_key_path = "/app/private-key.pem"
        
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.vehicle_id = None 
        

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
                self.vehicle_id = session.get('vehicle_id') 
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("No valid session file found")

    def save_session(self):
        """Persist tokens to disk to maintain the single-use OAuth chain."""
        try:
            session_data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "vehicle_id": self.vehicle_id,
                "updated_at": int(time.time())
            }
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=4)
            logger.info("Tesla tokens updated and saved to disk.")
        except Exception as e:
            logger.error(f"Failed to save Tesla session: {e}")

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
                    
                except VehicleOffline:
                    logger.debug("Tesla is asleep. Safely returning None.")
                    return None
                    
                except (OAuthExpired, Exception) as e:
                    # PROPER FIX: Explicitly catch OAuthExpired class or string matches
                    is_auth_error = isinstance(e, OAuthExpired) or "401" in str(e).lower() or "unauthorized" in str(e).lower()
                    
                    if is_auth_error:
                        logger.info("Tesla token expired. Attempting refresh...")
                        if await self._refresh_tokens_async(session):
                            # Rebuild the API objects with the fresh tokens
                            api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                            api.private_key = parsed_key
                            vehicle = VehicleSigned(api, self.vehicle_id)
                            
                            # Wrap the retry in a try/except so a failed retry doesn't crash main.py
                            try:
                                return await coro_func(api, vehicle, *args, **kwargs)
                            except Exception as retry_e:
                                logger.error(f"Retry failed after token refresh: {retry_e}")
                                return None
                                
                    logger.error(f"Tesla Request Failed: {e}")
                    return None
                    
        return asyncio.run(wrapper())

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
                if resp.status != 200: return False
                tokens = await resp.json()
                # PROPER FIX: Protect against 'None' payloads and eliminate brackets
                if not tokens: 
                    return False
                
                self.access_token = tokens["access_token"]
                self.refresh_token = tokens["refresh_token"]

                if not self.access_token:
                    return False
                
                self.save_session()
                return True
        except Exception:
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
        return data.get('response', {}).get('charge_state', {}).get('battery_level', 0)

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