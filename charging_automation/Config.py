# Charging Automation Config

import json


class Config:
    def __init__(self, config_file):
        self.config_file = config_file
        self.rivian_user = None
        self.rivian_pass = None
        self.solaredge_client_id = None
        self.solaredge_client_secret = None
        self.solaredge_site_id = None
        self.solaredge_redirect_uri = None
        self.homeassistant_url = None
        self.homeassistant_token = None
        self.emporia_load_entities = []
        self.night_time_start = None
        self.night_time_end = None

        with open(self.config_file) as f:
            data = json.load(f)
            self.rivian_user = data['rivian-user']
            self.rivian_pass = data['rivian-pass']
            self.solaredge_client_id = data['solaredge-client-id']
            self.solaredge_client_secret = data['solaredge-client-secret']
            self.solaredge_site_id = data['solaredge-site-id']
            self.solaredge_redirect_uri = data.get('solaredge-redirect-uri', 'http://localhost:8080/oauth/callback')
            self.homeassistant_url = data.get('homeassistant-url')
            self.homeassistant_token = data.get('homeassistant-token')
            self.emporia_load_entities = data.get('emporia-load-entities', [])
            self.night_time_start = data['night-time-start']
            self.night_time_end = data['night-time-end']

