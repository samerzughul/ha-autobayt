# Autobayt for Home Assistant

A Home Assistant integration for [Autobayt](https://autobayt.com) Wi-Fi wall switches and shutters, through the Autobayt cloud.

- One **light** per relay channel that drives a light (by its name, for example "Main Light" or "Dim") and one **switch** for anything else (for example a boiler), grouped into one device per physical switch, with model, firmware and the room set in the Autobayt app.
- **Shutters** appear as cover entities that show their position only. They **cannot be opened, closed or stopped from Home Assistant** until Autobayt provides a way to do it; see [Shutters](#shutters).
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

To change the password or server address later, open the integration, choose the three dots and **Reconfigure**. It must be the same Autobayt account. If Autobayt stops accepting the saved sign-in, Home Assistant shows a **Re-authenticate** notice instead.

## Options

Open the integration and choose **Configure**.

| Option | Default | Meaning |
| --- | --- | --- |
| Refresh every | 10 s | How often switch states are read from Autobayt for the whole home (one request per refresh). |
| Shutter model names | empty | Comma-separated model names to treat as shutters, if a shutter shows up as switches. |
| Words that mean a light | `light, lamp, dim, spot, led, chandelier` | A channel whose name contains one of these words is a light; the rest are switches. |
| Energy sensors | on | Power and today's energy for PM devices and the home. |
| Time zone for daily energy | Home Assistant's time zone | Decides when "today" starts for the energy sensors, for example `Asia/Amman`. |

After you switch something, the integration checks again after about 2, 5 and 10 seconds and shows the new state straight away while it waits for Autobayt to report it. If Autobayt does not confirm the change within about 15 seconds, the switch goes back to the reported state. When the server fails or asks to slow down, refreshes back off up to 5 minutes and return to normal once it answers again. One or two failed refreshes keep the last known states; after three in a row the entities show as unavailable until Autobayt answers again.

## Entities

| Entity | Unique id | Notes |
| --- | --- | --- |
| Light or switch per channel | `autobayt_<device id>_<channel>` | Named after the channel in the Autobayt app, and renamed when you rename it there (unless you named it in Home Assistant). Unavailable when Autobayt reports the device offline. Keeps its unique id if it moves between light and switch. |
| Shutter cover | `autobayt_<device id>_shutter` | Position only: 0 is closed, 100 is open. No controls. |
| Device power (W) | `autobayt_<device id>_power` | PM models. Read every 3 minutes. |
| Device energy today (kWh) | `autobayt_<device id>_energy_today` | PM models. Starts again at local midnight. |
| Home power, energy today | `autobayt_<location id>_power`, `..._energy_today` | On a "home" device. |

Energy values Autobayt marks as missing, stale or incomplete show as unavailable, never as zero.

## Shutters

**Shutters are read-only.** Home Assistant shows each shutter and the position Autobayt reports, but it cannot open, close, stop or move them. Use the Autobayt app for that.

Why: Autobayt's documented command only switches channels on and off. It has no way to say "open", "close", "stop" or "go to 40%", and a shutter's two motor channels (up and down) must never be switched like ordinary relays, because both could end up on at once. This integration does not guess at undocumented commands. Control will be added once Autobayt publishes a shutter command.

Shutter controllers are recognised by their model name. On a shutter wall plate the first two channels drive the motor and are never shown as switches; any further channel is the plate's light, shown as a light named for example "Dim light", and that light can be switched normally.

If a shutter shows up as two switches instead, add its model name (shown on the device page) under **Shutter model names**, and please open an issue with the model name so it can be recognised automatically.

## Devices that are added or removed

New devices added in the Autobayt app appear on the next refresh. A device that Autobayt no longer reports for three refreshes in a row is removed from Home Assistant; you can also remove such a device by hand from its device page. Energy sensors for a newly added power-metering device appear after reloading the integration.

## Known limitations

- Shutters are read-only (see above).
- Lights are on/off only; the integration does not dim.
- Everything goes through the Autobayt cloud, so nothing works while the home's internet or Autobayt's servers are down.
- States are read every 10 seconds by default, so a change made with a wall switch or the Autobayt app can take up to that long to show.

## Troubleshooting

- **Diagnostics**: on the integration's page choose **Download diagnostics**. It includes the fields Autobayt reports for each device and channel, with tokens, account, location and device ids removed.
- **Debug logs**: enable debug logging for the integration. Logs never contain your token, account or device ids.
- **Everything is unavailable**: Autobayt did not answer three refreshes in a row. It recovers by itself; check that the Autobayt app works.
- **A notice asks you to sign in again**: Autobayt stopped accepting the saved sign-in, for example after a password change. Follow the notice.

## Removing the integration

Open **Settings → Devices & services → Autobayt**, choose the three dots on the home and **Delete**. Then, if you installed it from the Marketplace, remove it there, or delete `custom_components/autobayt` and restart. The sign-in token is deleted with the entry; your devices and settings in the Autobayt app are not touched.

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
