import teslapy
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

class TeslaChargingData:
    def __init__(self, email, token_cache_dir='.tesla_tokens'):
        """Initialize with Tesla account email and token cache directory"""
        self.email = email
        self.token_cache_dir = Path(token_cache_dir)
        self.token_cache_dir.mkdir(exist_ok=True)
        self.token_file = self.token_cache_dir / f'{self.email}.json'
        
        # Initialize Tesla connection
        self.tesla = teslapy.Tesla(self.email, cache_file=str(self.token_file))
        self.load_token()

    def load_token(self):
        """Load token from cache if valid"""
        if self.token_file.exists():
            with open(self.token_file, 'r') as f:
                token_data = json.load(f)
                expires_at = datetime.fromtimestamp(token_data['created_at']) + timedelta(seconds=token_data['expires_in'])
                
                if datetime.now() < expires_at:
                    self.tesla.token = token_data
                    return True
        return False

    def save_token(self):
        """Save token to cache"""
        if self.tesla.token:
            with open(self.token_file, 'w') as f:
                json.dump(self.tesla.token, f)

    def authenticate(self):
        """Authenticate with Tesla account using OAuth2"""
        if not self.tesla.authorized:
            if self.load_token():
                try:
                    # Try to refresh token if near expiration
                    expires_at = datetime.fromtimestamp(self.tesla.token['created_at']) + \
                                timedelta(seconds=self.tesla.token['expires_in'])
                    if datetime.now() > expires_at - timedelta(minutes=5):
                        self.tesla.refresh_token()
                        self.save_token()
                    return
                except Exception as e:
                    print(f"Token refresh failed: {e}")

            # Initiate OAuth2 authentication
            print("Initiating Tesla authentication...")
            self.tesla.fetch_token()
            self.save_token()
            
    def get_charging_data(self):
        """Get current charging data for all vehicles"""
        self.authenticate()
        vehicles = self.tesla.vehicle_list()
        
        charging_data = []
        for vehicle in vehicles:
            data = {
                'vehicle_id': vehicle['id'],
                'state': vehicle['state'],
                'battery_level': vehicle['charge_state']['battery_level'],
                'charging_state': vehicle['charge_state']['charging_state'],
                'charge_limit_soc': vehicle['charge_state']['charge_limit_soc'],
                'time_to_full_charge': vehicle['charge_state']['time_to_full_charge'],
                'charge_rate': vehicle['charge_state']['charge_rate'],
                'charger_voltage': vehicle['charge_state']['charger_voltage'],
                'charger_actual_current': vehicle['charge_state']['charger_actual_current'],
                'charge_energy_added': vehicle['charge_state']['charge_energy_added'],
                'charge_port_door_open': vehicle['charge_state']['charge_port_door_open']
            }
            charging_data.append(data)
            
        return charging_data

    def get_charging_status(self):
        """Get simplified charging status"""
        data = self.get_charging_data()
        status = []
        for vehicle in data:
            status.append({
                'vehicle_id': vehicle['vehicle_id'],
                'charging': vehicle['charging_state'] == 'Charging',
                'battery_level': vehicle['battery_level'],
                'time_to_full_charge': vehicle['time_to_full_charge']
            })
        return status
