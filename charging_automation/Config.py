# Charging Automation Config

import json


class Config:
    def __init__(self, config_file):
        self.config_file = config_file
        self.rivian_user = None
        self.rivian_pass = None
        self.solaredge_api__key = None
        self.solaredge__site_id = None
        self.night_time_start = None
        self.night_time_end = None

        with open(self.config_file) as f:
            data = json.load(f)
            self.rivian_user = data['rivian-user']
            self.rivian_pass = data['rivian-pass']
            self.solaredge_api_key = data['solaredge-api-key']
            self.solaredge__site_id = data['solaredge-site-id']
            self.night_time_start = data['night-time-start']
            self.night_time_end = data['night-time-end']
