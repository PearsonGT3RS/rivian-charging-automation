# Solar Charging Automation for Rivian & Tesla

| WARNING: This script is provided as is, without any guarantees it will work for you. Use at your own risk.
| :---

This script automates EV charging using excess solar energy produced by a **SolarEdge** PV system. It dynamically
adjusts each vehicle's charging speed (in Amps) to align with the available surplus solar production, and it
**optimizes that surplus across two EVs simultaneously** — a **Rivian** and a **Tesla** — splitting the available
solar between them based on state of charge and hardware minimums.

Additionally, it can force charge to a configurable level at night in case solar power alone is not enough for your
daily needs.

<img src="https://github.com/ostap-korkuna/rivian-charging-automation/assets/44854323/e5781756-0f21-4e07-886c-8288c4bf8046" height="500"> &nbsp;
<img src="https://github.com/ostap-korkuna/rivian-charging-automation/assets/44854323/a0b3c539-b300-4fbf-b5b9-7a91d0d7f6ec" height="500"> &nbsp;
<img src="https://github.com/ostap-korkuna/rivian-charging-automation/assets/44854323/96393ac8-1590-437d-bd9d-c5851d172b60" height="500">


**Why not just use a native excess-solar EV charger?**

Native excess-solar chargers tend to be conservative and coarse — many can only change charging speed in large
increments. The chargers inside the Rivian and Tesla can make much finer adjustments (Rivian: 8A–48A in 2A steps;
Tesla: 5A–48A in 1A steps), so a software controller can track solar production far more tightly and waste less
surplus.

<img src="https://github.com/ostap-korkuna/rivian-charging-automation/assets/44854323/c6d2e0f3-d7a9-4c98-9203-a49ce4dae346" height="500">


## Requirements:
1) Rivian vehicle (tested on R1S) and/or Tesla vehicle (tested on Highland Model 3)
2) SolarEdge solar system (v2 API with OAuth 2.0)
3) Home Assistant with Emporia Vue energy sensors (for load and per-EV draw)
4) Raspberry Pi or similar always-on computer/server to run the automation script in Docker
5) (Optional) Hubitat home automation hub to monitor and switch charging automation modes from the mobile app

## How it works?
Every 15 minutes during the day the script reads the current PV production from the SolarEdge v2 API and the current
home load from Emporia Vue sensors exposed via Home Assistant. It computes the grid flow as `grid = load - pv`
(positive = import, negative = export) and derives the surplus available for the EVs.

Because the Emporia load already includes whatever the EVs are currently drawing, the script also reads each EV's
actual draw from its own dedicated Emporia circuit sensor (e.g. `sensor.rivian_charger_power_minute_average` and
`sensor.external_lighting_power_minute_average`). The total solar budget for the EVs is then:

    solar_for_ev = rivian_draw + tesla_draw + surplus

which is exactly the solar available for the EVs regardless of how much they are currently drawing.

That budget is split between the two vehicles using SOC-based rules (see [Solar Allocation](#solar-allocation)), and
each vehicle's charging speed is set via its native API:
- **Rivian** — Charging Schedule feature via the Rivian GraphQL API (no wake-up required).
- **Tesla** — Signed Commands via the Tesla Fleet API (wakes the car only when needed).

At night, the script can either charge to a configurable level or not charge at all if you want to use solar energy
only.

The algorithm intentionally does not use the home battery to charge the EVs during the day, and it will prioritize
charging the home battery if it's below 100%. This is because most home batteries are relatively small (compared to EV
batteries), so there would be little benefit, but it would complicate the algorithm quite a bit.

### Solar Allocation
When both vehicles are plugged in, the available solar budget is split by state of charge:
- Both below 50% → 50/50 split.
- One above 50% → 75% to the lower-SOC vehicle, 25% to the higher.
- A vehicle at 100% (or presumed full while asleep) gets 0%.
- If a vehicle's share is below its hardware minimum (Rivian 1920W / 8A, Tesla 1200W / 5A), those watts are
  reassigned to the other vehicle so no solar is wasted.
- If both vehicles are above 50% and there is no surplus, charging stops.

### Automation Modes
The automation has three modes:
- OFF - Automation off
- DEFAULT - Use excess solar during the day and charge at full speed at night (to a certain limit)
- SOLAR_ONLY - only charge during the day using excess solar

### Step-by-step Algorithm
1. Check if each charger is plugged in. Vehicles that are not plugged in are excluded from allocation.
2. If in "default mode" (charge at night): check if it's night time AND the EV battery state of charge is below the
   set limit — if yes, set to maximum Amperage and exit.
3. Read PV production from the SolarEdge v2 API and total load from Emporia Vue sensors via Home Assistant.
4. Compute grid flow: `grid = load - pv` (positive = import, negative = export).
5. Read each EV's actual draw from its dedicated Emporia circuit sensor.
6. Compute the total solar budget for the EVs: `solar_for_ev = ev_draw + (-grid)`.
7. Split `solar_for_ev` between the vehicles using the SOC-based rules in [Solar Allocation](#solar-allocation).
8. Convert each vehicle's watt allocation to Amps (rounded down to the vehicle's increment) and apply it via the
   vehicle's native API. If a vehicle's allocation is below its minimum, stop that vehicle's charge.
9. Wait 15 minutes before the next update during the day (5 minutes at night while actively charging).


## Setting Up

### 1. Check out the code

### 2. Create a config file and authenticate
Copy `charging_automation/config-example.json` as `/app/config.json` and fill in:

- **Rivian** credentials (`rivian-user`, `rivian-pass`)
- **SolarEdge v2 OAuth** app credentials (`solaredge-client-id`, `solaredge-client-secret`, `solaredge-site-id`,
  `solaredge-redirect-uri`)
- **Home Assistant** URL and long-lived access token (`homeassistant-url`, `homeassistant-token`)
- **Emporia Vue** sensor entity IDs:
  - `emporia-load-entities` — the top-level load sensors (must already aggregate the EV circuits)
  - `emporia-rivian-entity` — the Rivian charger circuit sensor
  - `emporia-tesla-entity` — the Tesla charger circuit sensor
- **Night-time** window (`night-time-start`, `night-time-end`)

Your `config.json` should look similar to this:

```json
{
    "rivian-user": "my-email@mail.com",
    "rivian-pass": "mysecretpass",
    "solaredge-client-id": "your SolarEdge v2 OAuth client ID",
    "solaredge-client-secret": "your SolarEdge v2 OAuth client secret",
    "solaredge-site-id": "your SolarEdge SiteID",
    "solaredge-redirect-uri": "http://localhost:8080/oauth/callback",
    "homeassistant-url": "http://homeassistant.local:8123",
    "homeassistant-token": "your long-lived Home Assistant access token",
    "emporia-load-entities": [
        "sensor.emporia_vue_channel_1_power",
        "sensor.emporia_vue_channel_2_power"
    ],
    "emporia-rivian-entity": "sensor.rivian_charger_power_minute_average",
    "emporia-tesla-entity": "sensor.external_lighting_power_minute_average",
    "night-time-start": 21,
    "night-time-end": 7
}
```

Rivian now requires 2-factor authentication. Run `python RivianSessionInitOTP.py` to initiate a Rivian API session.

SolarEdge v2 uses OAuth 2.0 with rotating refresh tokens. Complete the one-time browser authorize flow, then bootstrap
the token file:

```shell
cd charging_automation
python SolarEdgeAPI.py bootstrap /app/config.json <authorization_code>
```

The `redirect_uri` used in the authorize URL must match the value registered on the SolarEdge app exactly.

### 3. [Optional] Create `hubitat-config.json`
If using Hubitat to control your automation, copy `hubitat-config-example.json` to `hubitat-config.json` and update with
your Hubitat configuration, otherwise skip this step (and comment out the corresponding code in
`ChargingAutomation.py`). 

See [Hubitat Setup](#hubitat-setup) for details.

### 4. Test the script
```shell
cd charging_automation
pip install --no-cache-dir -r requirements.txt
python main.py 
```
If the algorithm iteration runs successfully, proceed to the next step to build and run it in docker.

### 5. Build docker image
```shell
docker build -t charging_automation .
```

### 6. Start the service using docker compose
```shell
docker compose up -d
```

### 7. Set your EVSE to always-on
Disable any smart features of your EVSE, so it's always feeding power to the car when plugged in.


## Hubitat Setup

If you have a Hubitat home automation hub, then you can use it to control your charging automation modes (see
[Automation Modes](#automation-modes)).

Here is how my Hubitat dashboard looks like:

<img src="https://github.com/ostap-korkuna/rivian-charging-automation/assets/44854323/e5781756-0f21-4e07-886c-8288c4bf8046" height="500">

### 1. Create virtual devices
Create 3 virtual devices:
1. Virtual Switch to enable/disable automation
2. Virtual Dimmer to enable and configure nigh-time charging limit
3. Virtual Omni Sensor to publish the info from the script to display on the dashboard

### 2. Configure Maker API

See [Maker API docs](https://docs2.hubitat.com/en/apps/maker-api)

### 3. Update `hubitat-config.json`
Set the following values:
- **host**: Local IP address / URL to your Hubitat hub 
- **api-id**: 3-digit ID of you Maker API instance (it's part of every Maker API URL in Hubitat)
- **token**: access token generated by Maker API
- **automation-on-switch-id**: ID of a virtual switch for enabling/disabling automation
- **night-charge-switch-id**: ID of a virtual dimmer for enabling night charging and setting charging limit at night
- **info-device-id**: ID of a virtual omni sensor for publishing information to the dashboard

### 4. Complete the remaining steps from [Setting Up](#setting-up) section

### 5. Create a dashboard with your virtual switches
See example dashboard above.


## How to turn this OFF?
If you want to completely turn OFF and revert any changes made by this script, follow the steps below:

1. Stop the docker container using `docker stop` command
2. Go to your Rivian App and disable or modify the Charging Schedule set by this automation
3. Reconfigure your EVSE to the desired settings (i.e. disable "always-on")


# Credits
Thank you [kaedenbrinkman](https://github.com/kaedenbrinkman) for publishing [Rivian API Documentation](https://github.com/kaedenbrinkman/rivian-api)

Thank you to the [tesla-fleet-api](https://github.com/Teslemetry/python-tesla-fleet-api) project for the Tesla Fleet API client.
