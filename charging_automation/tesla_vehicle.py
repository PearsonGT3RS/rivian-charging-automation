import teslapy
from pathlib import Path
import json
from datetime import datetime, timedelta

class TeslaVehicleData:
    def __init__(self, email, token_cache_dir='.tesla_tokens'):
        """Initialize Tesla vehicle data access"""
        self.email = email
        self.token_cache_dir = Path(token_cache_dir)
        self.token_cache_dir.mkdir(exist_ok=True)
        self.token_file = self.token_cache_dir / f'{self.email}.json'
        self.tesla = teslapy.Tesla(self.email, cache_file=str(self.token_file))
        self.authenticate()

    def authenticate(self):
        """Handle Tesla OAuth2 authentication"""
        if not self.tesla.authorized:
            if self._load_token():
                try:
                    # Refresh token if near expiration
                    expires_at = datetime.fromtimestamp(self.tesla.token['created_at']) + \
                                timedelta(seconds=self.tesla.token['expires_in'])
                    if datetime.now() > expires_at - timedelta(minutes=5):
                        self.tesla.refresh_token()
                        self._save_token()
                    return
                except Exception as e:
                    print(f"Token refresh failed: {e}")

            print("Initiating Tesla authentication...")
            self.tesla.fetch_token()
            self._save_token()

    def _load_token(self):
        """Load cached token if valid"""
        if self.token_file.exists():
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
                expires_at = datetime.fromtimestamp(token_data['created_at']) + \
                            timedelta(seconds=token_data['expires_in'])
                if datetime.now() < expires_at:
                    self.tesla.token = token_data
                    return True
        return False

    def _save_token(self):
        """Save token to cache"""
        if self.tesla.token:
            with open(self.token_file, 'w') as f:
                json.dump(self.tesla.token, f)

    def get_vehicle_data(self):
        """Get detailed data for all vehicles"""
        self.authenticate()
        vehicles = self.tesla.vehicle_list()
        
        vehicle_data = []
        for vehicle in vehicles:
            data = vehicle.get_vehicle_data()
            vehicle_data.append({
                'id': data['id'],
                'name': data['vehicle_config']['car_type'],
                'state': data['state'],
                'vin': data['vin'],
                'odometer': data['vehicle_state']['odometer'],
                'software_version': data['vehicle_state']['car_version'],
                'battery': {
                    'level': data['charge_state']['battery_level'],
                    'range': data['charge_state']['battery_range'],
                    'charging_state': data['charge_state']['charging_state']
                },
                'climate': {
                    'inside_temp': data['climate_state']['inside_temp'],
                    'outside_temp': data['climate_state']['outside_temp'],
                    'is_climate_on': data['climate_state']['is_climate_on']
                }
            })
        return vehicle_data

    def display_vehicle_summary(self):
        """Display formatted vehicle information"""
        data = self.get_vehicle_data()
        if not data:
            print("No vehicles found")
            return

        for vehicle in data:
            print(f"\nVehicle: {vehicle['name']} ({vehicle['vin']})")
            print(f"State: {vehicle['state'].capitalize()}")
            print(f"Odometer: {vehicle['odometer']:.1f} miles")
            print(f"Software: {vehicle['software_version']}")
            print("\nBattery:")
            print(f"  Level: {vehicle['battery']['level']}%")
            print(f"  Range: {vehicle['battery']['range']:.1f} miles")
            print(f"  Status: {vehicle['battery']['charging_state']}")
            print("\nClimate:")
            print(f"  Inside: {vehicle['climate']['inside_temp']}°C")
            print(f"  Outside: {vehicle['climate']['outside_temp']}°C")
            print(f"  Climate Control: {'On' if vehicle['climate']['is_climate_on'] else 'Off'}")
            print("-" * 40)
