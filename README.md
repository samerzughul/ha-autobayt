# Autobayt for Home Assistant

A Home Assistant integration for [Autobayt](https://autobayt.com) Wi-Fi wall switches and shutters, through the Autobayt cloud.

- One **switch** per relay channel, grouped into one device per physical switch, with model, firmware and the room set in the Autobayt app.
- **Shutters** appear as cover entities showing their position (read-only for now, see below).
- **Power and today's energy** for power-metering (PM) models and for the whole home.
- Sign in with your Autobayt account once. Home Assistant keeps a sign-in token, not your password, and asks you to sign in again if Autobayt stops accepting it.
- No firmware updates or other device administration: pair and update devices in the Autobayt app.

This is a community project and is not made or supported by Autobayt.

## Install

### Home Assistant Marketplace

1. In Home Assistant, open **Settings → Marketplace**.
2. Choose **Add from link** and paste `https://github.com/samerzughul/ha-autobayt`.
3. Install **Autobayt** and restart Home Assistant.

### Manually

Copy `custom_components/autobayt` into the `custom_components` folder of your Home Assistant configuration and restart.

## Set up

1. **Settings → Devices & services → Add integration → Autobayt**.
2. Enter the email and password of your Autobayt app account.
3. If the account has more than one home, pick one. Add the integration again for each further home.

Each device is placed in an area named after its Autobayt room when it is first added.

**Advanced → Server address** defaults to `https://api.autobayt.com`. Only change it if Autobayt gives you a different address.

## Options

Open the integration and choose **Configure**.

| Option | Default | Meaning |
| --- | --- | --- |
| Refresh every | 10 s | How often switch states are read from Autobayt for the whole home (one request per refresh). |
| Shutter model names | empty | Comma-separated model names to treat as shutters, if a shutter shows up as switches. |
| Energy sensors | on | Power and today's energy for PM devices and the home. |
| Time zone for daily energy | `Asia/Amman` | Decides when "today" starts for the energy sensors. |

After you switch something, the integration checks again after about 2, 5 and 10 seconds and shows the new state straight away while it waits for Autobayt to report it. If Autobayt does not confirm the change within about 15 seconds, the switch goes back to the reported state. When the server fails or asks to slow down, refreshes back off up to 5 minutes and return to normal once it answers again.

## Entities

| Entity | Unique id | Notes |
| --- | --- | --- |
| Switch per channel | `autobayt_<device id>_<channel>` | Unavailable when Autobayt reports the device offline. |
| Shutter cover | `autobayt_<device id>_shutter` | Position 0 is closed, 100 is open. |
| Device power (W) | `autobayt_<device id>_power` | PM models. Read every 3 minutes. |
| Device energy today (kWh) | `autobayt_<device id>_energy_today` | PM models. Starts again at local midnight. |
| Home power, energy today | `autobayt_<location id>_power`, `..._energy_today` | On a "home" device. |

Energy values Autobayt marks as missing, stale or incomplete show as unavailable, never as zero.

## Shutters

Shutter controllers are recognised by their model name. They show the position Autobayt reports, but **cannot be opened, closed or stopped from Home Assistant yet**: Autobayt has not published a way to control shutters through its cloud, and driving a shutter's motor channels as two plain switches could run both directions at once. Control will be added when Autobayt provides it. Until then use the Autobayt app.

If a shutter shows up as two switches instead, add its model name (shown on the device page) under **Shutter model names**, and please open an issue with the model name so it can be recognised automatically.

## Troubleshooting

- **Diagnostics**: on the integration's page choose **Download diagnostics**. Tokens, account, location and device ids are removed from the file.
- **Debug logs**: enable debug logging for the integration. Logs never contain your token, account or device ids.

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests run against a fake Autobayt backend; nothing talks to the real cloud.

## License

MIT
