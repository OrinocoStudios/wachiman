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
| **Radar: network** | Which app connects where (domain, company, country) and how much it sends | **Next** |
| Radar: mic and camera | Which app turned them on, when and for how long | Planned |
| Radar: permissions | Apps granted keyboard (Input Monitoring, Accessibility) or screen recording access | Planned |
| Radar: devices | New USB, Bluetooth and Wi-Fi networks | Planned |
| Web view | Local page with the history | Planned |

Platform: **macOS** first. Linux is a goal once the macOS modules are stable.

## Roadmap

1. Repository, licence and layout. *(this commit)*
2. Radar: network history per app.
3. Radar: microphone and camera.
4. Radar: keyboard and screen permissions.
5. Radar: devices.
6. Port the private Guard, Stealer watch and Memory watch modules.
7. Installer and local web view.

## Contributing

Issues and pull requests are welcome. Keep the principles above: if a feature needs to record content rather than metadata, it does not belong here.

## Licence

[MIT](LICENSE) © Orinoco Studios
