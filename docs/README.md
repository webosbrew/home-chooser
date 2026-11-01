# Home Chooser developer documentation

These pages are for people working on Home Chooser or writing a launcher for it. The
[project README](../README.md) is for TV owners.

| Page | What it covers |
| --- | --- |
| [Architecture](architecture.md) | The programs, the firmware pieces they touch, the HOME key per webOS family, the home gate decision and the daemon lifecycle, with diagrams |
| [Writing a launcher](launcher-api.md) | The launcher contract and API level 2: checklist, appinfo, selection requests, methods, errors, platform calls, per-release notes, testing |
| [Launcher API description](../api/README.md) and [example launcher](../examples/web-launcher/README.md) | The OpenRPC file, the generated JS client and browser mock, and a small web launcher using them |
| [The settings app](settings-app.md) | The UI, root access, what is written on each release, persistence, removal handling, the stock home's preload policy |
| [The root service](service.md) | `homechooser-service` and `homechooser-daemon`: package, source layout, elevation, methods, caller checks, access files, allow-lists, logs |
| [Live Home key filter, webOS 6–9](home-key-hook.md) | The generated key filter, the configd transaction and restoration |
| [Home gate, webOS 6–9](home-gate.md) | The binary that stands in for stock Home: decision table, self-release, mount lifecycle |
| [Building and testing](build.md) | Toolchain, packaging, build variables, version source, offline tests |
| [End-to-end runner](../e2e/README.md) | The device test runner and the launcher fixtures |

## Research

Concluded studies whose conclusions the code relies on:

* [The Home key and the home app, webOS 5 to 26](research/home-key-and-home-app.md): how each
  release picks and launches its home app, the settings record and system profile, permissions,
  and how the 6–9 key filters are loaded.
* [System font verification](research/system-font-verification.md): why the package ships without
  Moonstone's bundled fonts.

Open questions, planned work and test campaigns are tracked in GitHub issues, not in these pages.
