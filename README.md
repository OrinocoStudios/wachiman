# Wachiman

> *Wachiman* (from the English "watchman") is what people in Venezuela and Peru call the night guard.

Wachiman tells you **what your computer does when you are not looking**: who uses the microphone or camera, which apps talk to the internet and where to, which apps can read your keyboard or screen, and what gets plugged in. Everything is stored locally, nothing leaves your machine, and alerts reach you on Telegram.

It is built for two kinds of people:

- **Everyone:** plain-language alerts only when something is new or unusual. For example: *"A new app can read your keyboard: X. Do you recognise it?"*, plus a short daily summary.
- **Technical users:** the full history, a `radar` command line and a local web page, to query and audit what happened.

## Principles

- **Local first.** The history lives in a SQLite file on your disk. No cloud, no account, no telemetry.
- **No spyware to catch spyware.** Wachiman records *metadata* (which app, when, how long, where to) and **never** your keystrokes, audio, screen contents or message bodies.
- **Changes, not signatures.** It does not try to be an antivirus. It learns what is normal on *your* machine and tells you when that changes: a new app reaching a new country, a new keyboard permission, a new login item.
- **Boring and auditable.** Small shell and Python scripts, no AI in the detection loop, readable in one sitting.

## Status

Early. The first modules run on the author's Mac and are being cleaned up for public use.

| Module | What it does | State |
|---|---|---|
| Guard | Alert plus webcam photos when someone touches the computer while the screen is locked | Running privately, being ported |
| Stealer watch | New LaunchAgents and login items, stealer artefacts, known command-and-control domains | Running privately, being ported |
| Memory watch | Memory pressure and swap rate alerts | Running privately, being ported |
| **Radar: network** | Which app connects where (company and country) | **Usable** (CLI, alerts) |
| **Radar: mic and camera** | Which app used the mic, camera or screen recording, when and for how long | **Usable** |
| Radar: permissions | Apps granted keyboard (Input Monitoring, Accessibility) or screen recording access | Planned |
| Radar: devices | New USB, Bluetooth and Wi-Fi networks | Planned |
| Web view | Local page with the history | Planned |

Platform: **macOS** first. Linux is a goal once the macOS modules are stable.

## Try the network radar

Needs only the `python3` and `lsof` that ship with macOS. Nothing to install.

```sh
git clone https://github.com/OrinocoStudios/wachiman.git
cd wachiman
bin/wachiman radar geo-update   # once: offline database of who owns each IP (~8 s)
bin/wachiman radar scan         # first scan learns what is normal, reports nothing
bin/wachiman radar watch        # keeps scanning; reports only what is new
```

Or leave it running in the background (macOS; starts again at every login):

```sh
bin/wachiman service install     # uninstall | status
```

Then, any time:

```sh
bin/wachiman radar net --since 24h        # which app reached which company and country
bin/wachiman radar net --app Slack
bin/wachiman radar traffic --since 24h    # bytes sent and received per app
bin/wachiman radar sensors --since 7d     # who used the mic, camera or screen recording
bin/wachiman radar events --since 7d      # what was worth an alert
```

What counts as news, on purpose only four things so it stays quiet:

- an app that never used the internet before does so now;
- a known app reaches a **country** it never reached before;
- an app uploads more than `WACHIMAN_UPLOAD_ALERT_MB` (default 100 MB) within one hour;
- an app uses the **microphone, camera or screen recording** for the first time.

Mic and camera history comes from the same source as the orange and green dots in the menu bar: macOS logs every change together with the app responsible. Wachiman reads that log (`log show`, no special permission) and never touches the sensors themselves. The first scan picks up the last minute only; history starts when the radar starts.

Upload totals come from `nettop` and are a lower bound: a connection that opens and closes entirely between two scans is not counted. A sustained upload, which is what data theft looks like, is.

**CC is the country where the company owning the IP is registered**, not where the server physically sits. A Google server in Ireland shows as `US`. That is what matters for "who gets my data", and it needs no online lookups.

## Settings and files

Everything lives under `~/.wachiman/` by default; nothing is tied to a user name or a fixed path.

| Variable | Default | What |
|---|---|---|
| `WACHIMAN_HOME` | `~/.wachiman` | data folder |
| `WACHIMAN_DB` | `$WACHIMAN_HOME/radar.db` | history (SQLite) |
| `WACHIMAN_GEO_DB` | `$WACHIMAN_HOME/geo.db` | IP owner database |
| `WACHIMAN_RADAR_INTERVAL` | `30` | seconds between scans |
| `WACHIMAN_TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | empty | alerts on Telegram (optional) |
| `WACHIMAN_UPLOAD_ALERT_MB` | `100` | alert when one app uploads more than this in an hour (`0` = off) |
| `WACHIMAN_LANG` | `en` | alert language: `en`, `es` |

Put them in `$WACHIMAN_HOME/config.env` (see [config.env.example](config.env.example)) or export them; the environment wins.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

## Roadmap

1. Repository, licence and layout. ✅
2. Radar: network history per app, bytes sent, background service. ✅
3. Radar: microphone, camera and screen recording. ✅
4. Radar: keyboard and screen permissions.
5. Radar: devices.
6. Port the private Guard, Stealer watch and Memory watch modules.
7. Installer and local web view.

## Contributing

Issues and pull requests are welcome. Keep the principles above: if a feature needs to record content rather than metadata, it does not belong here.

## Licence

[MIT](LICENSE) © Orinoco Studios
