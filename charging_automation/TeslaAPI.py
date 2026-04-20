import logging
import json
import asyncio
import aiohttp
import time
from tesla_fleet_api import TeslaFleetApi
from tesla_fleet_api.tesla.vehicle.signed import VehicleSigned
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class TeslaAPI:
    AMPS_MIN = 5
    AMPS_MAX = 48
    
    def __init__(self, config_file="/app/config.json", session_file="/sessions/tesla-session.json"):
        # Reverted to your exact paths
        self.config_file = config_file
        self.session_file = session_file
        self.private_key_path = "/app/private-key.pem"
        
        self.client_id = None
        self.client_secret = None
        self.access_token = None
        self.refresh_token = None
        self.vehicle_id = None 
        
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
        """Persist new tokens to survive container restarts."""
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
        """Bridge between sync main loop and async library to fix RuntimeWarnings."""
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
                except Exception as e:
                    if "401" in str(e).lower() or "unauthorized" in str(e).lower():
                        if await self._refresh_tokens_async(session):
                            api = TeslaFleetApi(session=session, access_token=self.access_token, region="na")
                            api.private_key = parsed_key
                            vehicle = VehicleSigned(api, self.vehicle_id)
                            return await coro_func(api, vehicle, *args, **kwargs)
                    
                    if "offline" in str(e).lower():
                        raise
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
                self.access_token = tokens["access_token"]
                self.refresh_token = tokens["refresh_token"]
                self.save_session()
                return True
        except Exception:
            return False

    # --- Commands (Public Sync Bridges) ---

    def get_state(self):
        """Passive check of vehicle state."""
        try:
            # Fixed attribute: api.vehicles() returns the list
            result = self._run_async(lambda api, _: api.vehicles())
            for v in result.get('response', []):
                # Using vehicle_id as identifier
                if v.get('vin') == self.vehicle_id or v.get('id_s') == self.vehicle_id:
                    return v.get('state', 'unknown')
            return "unknown"
        except Exception:
            return "offline"

    def is_charger_connected(self):
        state = self.get_state()
        return state != "offline"

    def wake_up(self):
        """Wakes the vehicle and waits for telemetry to stabilize."""
        async def _wake(api, vehicle):
            return await vehicle.wake_up()
        
        logger.info("Sending wake command to Tesla...")
        result = self._run_async(_wake)
        
        # Highland-specific stabilization delay
        # This prevents 'VehicleOffline' crashes in the next step of the loop
        logger.info("Waiting 20 seconds for Highland telemetry to boot...")
        time.sleep(20) 
        
        return result

    def get_vehicle_data(self):
        """Full telemetry data poll."""
        return self._run_async(lambda _, v: v.vehicle_data())

    def charge_start(self):
        return self._run_async(lambda _, v: v.charge_start())

    def charge_stop(self):
        return self._run_async(lambda _, v: v.charge_stop())

    def set_charging_amps(self, amps):
        amps = max(self.AMPS_MIN, min(amps, self.AMPS_MAX))
        return self._run_async(lambda _, v: v.set_charging_amps(amps))

    def is_charging(self):
        try:
            data = self.get_vehicle_data()
            state = data.get('response', {}).get('charge_state', {}).get('charging_state')
            return state == 'Charging'
        except Exception:
            return False

    def get_battery_level(self):
        try:
            data = self.get_vehicle_data()
            return data.get('response', {}).get('charge_state', {}).get('battery_level', 0)
        except Exception:
            return 0

    def get_current_schedule_amp(self):
        try:
            data = self.get_vehicle_data()
            return data.get('response', {}).get('charge_state', {}).get('charge_amps', 0)
        except Exception:
            return 0

    # --- Schedule-Specific Aliases ---

    def set_schedule_amps(self, amps):
        return self.set_charging_amps(amps)

    def set_schedule_off(self):
        return self.charge_stop()
        
    def set_schedule_default(self):
        return self.set_schedule_amps(self.AMPS_MAX)