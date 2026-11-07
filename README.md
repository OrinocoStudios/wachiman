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

Early. Every module below runs on the author's Mac.

| Module | What it does | State |
|---|---|---|
| **Guard** | Alert plus webcam photos when someone touches the computer while the screen is locked | **Usable** |
| **Stealer watch** | New or modified LaunchAgents and login items, stealer artefacts, known command-and-control domains | **Usable** |
| **Memory watch** | Memory pressure and swapout rate alerts | **Usable** |
| **Telegram gateway** | Drive all of the above from your phone with fixed commands | **Usable** |
| **Radar: network** | Which app connects where (company and country) | **Usable** (CLI, alerts) |
| **Radar: mic and camera** | Which app used the mic, camera or screen recording, when and for how long | **Usable** |
| **Radar: keyboard** | Programs reading the keyboard right now (event taps) and who signed them | **Usable** |
| **Radar: devices** | USB devices, paired Bluetooth devices and networks (Wi-Fi name or router) | **Usable** |
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
bin/wachiman radar keyboard               # programs seen reading the keyboard, and their signer
bin/wachiman radar devices --kind usb     # usb | bt | net
bin/wachiman radar events --since 7d      # what was worth an alert
```

What counts as news, on purpose only six things so it stays quiet:

- an app that never used the internet before does so now;
- a known app reaches a **country** it never reached before;
- an app uploads more than `WACHIMAN_UPLOAD_ALERT_MB` (default 100 MB) within one hour;
- an app uses the **microphone, camera or screen recording** for the first time;
- a program never seen before starts **reading the keyboard**;
- a **USB device**, **paired Bluetooth device** or **network** never seen before shows up. A USB stick that presents itself as a keyboard ("BadUSB") is how someone with 30 seconds at your desk types commands into your Mac.

A keylogger on macOS has to install an *event tap*. CoreGraphics lists every active tap and its owner without any permission, so Wachiman sees them directly, and records who signed the program: Apple, a named developer, or nobody. An unsigned program reading keystrokes is the classic stealer pattern. Launchers, shortcut tools and accessibility apps legitimately do this too; the alert tells you *which* one, you decide.

Mic and camera history comes from the same source as the orange and green dots in the menu bar: macOS logs every change together with the app responsible. Wachiman reads that log (`log show`, no special permission) and never touches the sensors themselves. The first scan picks up the last minute only; history starts when the radar starts.

Upload totals come from `nettop` and are a lower bound: a connection that opens and closes entirely between two scans is not counted. A sustained upload, which is what data theft looks like, is.

**CC is the country where the company owning the IP is registered**, not where the server physically sits. A Google server in Ireland shows as `US`. That is what matters for "who gets my data", and it needs no online lookups.

## Guard, Stealer watch, Memory watch

Plain shell loops (no AI in them) that alert you on Telegram. Set `WACHIMAN_TELEGRAM_BOT_TOKEN` and `WACHIMAN_TELEGRAM_CHAT_ID` first (see below); without them they still run and write their log, they just cannot message you. Files go under `$WACHIMAN_HOME/guard`, `stealerwatch` and `memwatch`.

### Guard

```sh
bin/wachiman guard start-nolock   # start watching, screen stays as it is (good for a first test)
bin/wachiman guard start          # start watching and lock the screen now
bin/wachiman guard status | stop
```

While the screen is locked, an **idle edge** (still for at least 60 s, then activity within 10 s) sends an alert and takes three webcam photos with `ffmpeg` (`brew install ffmpeg`; found on `PATH`, or set `WACHIMAN_FFMPEG`). At most one burst per minute. Photos are named `YYYY-MM-DD_HH-MM-SS_N.jpg` in `$WACHIMAN_HOME/guard/` and sent to Telegram.

- **Paranoid by default.** If you unlock within 30 s of a burst, Guard does *not* delete the photos, because unlocking only proves it was you if your Mac really asks for a password. Once you have confirmed that by hand, set `WACHIMAN_GUARD_PARANOID=0` to delete the photos on a quick unlock.
- The first time, macOS asks the app that launched Guard (your terminal) for camera access.
- The camera is chosen by name (`WACHIMAN_CAM_NAME`, matched against `ffmpeg`'s device list at every start) because avfoundation indexes change when USB devices come and go; `WACHIMAN_CAM` is only the fallback index.
- Lock state is read from the loginwindow shield (`shieldWindowShowing:1/0` in the unified log), not from `ioreg`, which is wrong on recent macOS. `wachiman guard status` shows which source it used. Set `WACHIMAN_GUARD_DEBUG=1` for one line per poll in `guard.debug`.
- Locking uses `SACLockScreenImmediate` through `python3` ctypes; no Accessibility permission needed.
- The camera LED lights up during a burst and cannot be disabled.

### Stealer watch

```sh
bin/wachiman stealerwatch baseline   # learn what is normal here (also what you run after accepting a change)
bin/wachiman stealerwatch scan       # one pass on screen, sends nothing
bin/wachiman stealerwatch watch      # loop every 5 minutes, alert on changes only
```

It does not match malware signatures. It watches the places a stealer has to write to survive a reboot: LaunchAgents/LaunchDaemons (new **or modified**, by hash), login items, loot files such as `/tmp/osalogging.zip`, connections to known command-and-control domains (`WACHIMAN_STEALER_C2_DOMAINS`), and unsigned, quarantined executables dropped under `~/Library`. Whatever exists when the baseline is taken never alerts; each finding alerts once per hour.

### Memory watch

```sh
bin/wachiman memwatch now      # pressure, free memory and the top 5 apps by memory
bin/wachiman memwatch watch    # loop every 20 s, alert on WARN / CRIT / recovery
```

Alerts come from the kernel's free-memory percentage (WARN at 20%, CRIT at 10%) and from the swapout *rate* (25 MB/s), the latter only when pressure is already below 35%. **Swap used never alerts**: macOS never shrinks it while the Mac stays up, so it is shown as context only. Recovery is announced only after pressure climbs 5 points past the WARN threshold, so the bot does not flap.

### Telegram gateway

```sh
bin/wachiman gateway run                   # long-polling bot, one process
bin/wachiman gateway run --once /status    # run one command and exit (testing)
```

Commands: `/status /on /test /off /lock /log /debug /photos /last /deletephotos /mem /top /thresholds /memtest /stealer /help`. Spanish aliases also work (`/estado /prueba /bloquea /fotos /ultimas /borrafotos /umbrales /ayuda`). Commands are fixed: nothing you type after the command reaches a shell. Only your configured chat is answered; any other chat is ignored and logged. `/deletephotos` must be sent twice within two minutes. The Telegram "/" menu and `/help` are built from the same table.

To keep the gateway and the two watchers running in the background (macOS launchd, label `io.orinoco.wachiman.*`; no secrets are written into the agent):

```sh
bin/wachiman service install --agent gateway      # also: memwatch, stealerwatch (radar is the default)
```

Guard is deliberately not a background service: it takes photos, so you start it yourself (or with `/on`).

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
| `WACHIMAN_TELEGRAM_API` | `https://api.telegram.org` | Bot API base URL (for tests) |
| `WACHIMAN_GUARD_POLL` / `_IDLE_ACTIVE` / `_IDLE_QUIET` | `3` / `10` / `60` | Guard: seconds between polls; idle below this = activity; idle at least this before = edge |
| `WACHIMAN_GUARD_BURST_COOLDOWN` / `_GRACE` | `60` / `30` | Guard: seconds between bursts; seconds to unlock after a burst |
| `WACHIMAN_GUARD_PARANOID` | `1` | `0` deletes photos on a quick unlock (only if your Mac asks for a password) |
| `WACHIMAN_GUARD_DEBUG` / `_SKIP_FRAMES` / `_PHOTO_GAP` | `0` / `12` / `1` | Guard: per-poll debug line; black frames to skip; seconds between photos |
| `WACHIMAN_CAM_NAME` / `WACHIMAN_CAM` / `WACHIMAN_FFMPEG` | empty / `0` / from `PATH` | camera by name, fallback index, ffmpeg binary |
| `WACHIMAN_MEM_POLL` / `_COOLDOWN` | `20` / `900` | Memory watch: seconds between samples; between repeats of the same level |
| `WACHIMAN_MEM_PRESSURE_WARN` / `_CRIT` | `20` / `10` | alert when the kernel's free % is at or below |
| `WACHIMAN_MEM_SWAPOUT_WARN_MBS` / `_SWAPOUT_GATE_PRESS` / `_RECOVER_MARGIN` | `25` / `35` / `5` | swapout MB/s that warns, only below this pressure %; hysteresis points |
| `WACHIMAN_STEALER_POLL` / `_COOLDOWN` | `300` / `3600` | Stealer watch: seconds between passes; between repeats of one finding |
| `WACHIMAN_STEALER_C2_DOMAINS` | built-in list | space-separated domains to check for live connections |

Put them in `$WACHIMAN_HOME/config.env` (see [config.env.example](config.env.example)) or export them; the environment wins.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

## Roadmap

1. Repository, licence and layout. ✅
2. Radar: network history per app, bytes sent, background service. ✅
3. Radar: microphone, camera and screen recording. ✅
4. Radar: programs reading the keyboard. ✅
5. Radar: USB, Bluetooth and networks. ✅
6. Guard, Stealer watch, Memory watch and the Telegram gateway. ✅
7. Installer and local web view.

## Contributing

Issues and pull requests are welcome. Keep the principles above: if a feature needs to record content rather than metadata, it does not belong here.

## Licence

[MIT](LICENSE) © Orinoco Studios
