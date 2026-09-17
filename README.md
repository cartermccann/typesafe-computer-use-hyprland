# typesafe-computer-use (Hyprland / NixOS fork)

Fork of [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use) with a **Hyprland** platform adapter.

Same loop as upstream:

```
grim capture → OCR → compact state → TypeSafe Jev Choice → hyprctl / ydotool action
```

~$0.0002 per decision. No screenshot to Opus.

Upstream is macOS-only (`macos.py` / Quartz / ocrmac). This fork keeps that file and adds `hyprland.py`, selected via `platform.py`.

## Why `hyprctl`

On Hyprland, `hyprctl` is the right control plane:

| Need | Tool |
|---|---|
| Active window class / title / geometry / pid | `hyprctl -j activewindow` |
| Focus a client | `hyprctl dispatch focuswindow address:…` |
| Launch browser / URL | `hyprctl dispatch exec -- …` |
| Cursor position (abort corner) | `hyprctl cursorpos` |
| Monitor scale | `hyprctl -j monitors` |
| Screenshot | `grim` |
| Click / key / type | `ydotool` (+ `ydotoold`) or `wtype` for text |
| OCR | `tesseract` via `pytesseract` |

## Install (NixOS / Hyprland)

System packages (flake `devShell`, or your system config):

```nix
# flake.nix devShell — or add to environment.systemPackages / home.packages
[
  pkgs.grim
  pkgs.tesseract
  pkgs.ydotool
  pkgs.wtype
  pkgs.hyprland          # provides hyprctl
]
```

```bash
# enter the shell (optional flake)
nix develop

# Python deps
uv sync --extra hyprland
cp .env.example .env   # TYPESAFE_API_KEY=…
export CLICKER_PLATFORM=hyprland
```

`ydotool` needs `ydotoold` with uinput access. On NixOS that usually means enabling the service / udev rules for your user (see ydotool NixOS wiki notes). Without it, dry-run still works; `--act` clicks will fail.

### Arch (also fine)

```bash
sudo pacman -S grim tesseract tesseract-data-eng ydotool wtype
```

| variable | required | purpose |
|---|---|---|
| `TYPESAFE_API_KEY` | yes | every Jev decision |
| `CLICKER_PLATFORM` | no | force `hyprland` / `macos` (Linux defaults to hyprland) |
| `ANTHROPIC_API_KEY` | no | only for free-text `type_text` |
| `CLICKER_EMAIL` | no | enables `type_email` |
| `CLICKER_BROWSER` | no | e.g. `firefox`, `chromium`, `google-chrome-stable` |

## Use

```bash
export CLICKER_PLATFORM=hyprland
uv run clicker "open the Playground"                 # dry run
uv run clicker "open the Playground" --act           # drives the machine
uv run clicker-inspect "any goal"                    # capture + annotated dump
```

**Stop a live run:** Ctrl-C, or slam the pointer into the **top-left corner** (same abort as macOS).



## Your browsers (not a separate automation browser)

The Hyprland adapter drives **whatever you already use** — Firefox, Zen, Chromium, Chrome, Brave, Vivaldi, Edge.

| Action | What it does |
|---|---|
| Focus | `hyprctl` finds an open window by class and focuses it |
| Open URL | `exec <your-browser> <url>` so the tab opens in **that profile** |
| Read URL | Chromium-family via CDP; Firefox/Chromium also via AT-SPI address bar |

Set the browser:

```bash
export CLICKER_BROWSER=google-chrome   # or chrome, firefox, chromium, brave, zen, …
```

If unset on Linux, it auto-picks the first installed browser from that list.


### NixOS Chrome tip

```nix
# home.packages / environment.systemPackages
pkgs.google-chrome
# or unfree: pkgs.google-chrome
```

Launch with debugging so TypeSafe can read the active tab URL (same profile):

```bash
google-chrome-stable --remote-debugging-port=9222
# CLICKER_BROWSER=google-chrome   # or just "chrome"
```

### Read the active tab URL (Chrome / Chromium / Brave)

CDP needs a debugging port on **your existing profile** (not a throwaway one):

```bash
# one-shot (keeps default profile)
google-chrome-stable --remote-debugging-port=9222
# or: chromium --remote-debugging-port=9222
```

NixOS / Hyprland bind example:

```bash
# hyprland.conf
bind = $mainMod, B, exec, google-chrome-stable --remote-debugging-port=9222
```

Optional: `export CLICKER_CDP_PORT=9222`

Firefox URL reads go through AT-SPI (install `at-spi2-core` / enable accessibility). No separate Firefox profile required.

## Layout

```
typesafe_computer_use/
  platform.py     picks macos vs hyprland
  macos.py        Quartz / AX / AppleScript (upstream)
  hyprland.py     hyprctl + grim + ydotool/wtype
  decide.py       TypeSafe Jev Choices (unchanged)
  actions.py      executes decisions (imports platform)
  perception.py   OCR (ocrmac on Mac, tesseract on Linux)
  runner.py       step loop
```

## Linux gaps (honest)

1. **Focused text field** — macOS uses Accessibility; Hyprland adapter currently stubs a window-level `Field`. Full AT-SPI wiring is next if you need reliable `type_text` verification.
2. **Browser URL** — AppleScript on Mac; here it’s best-effort (often `None`). CDP against Chrome is the proper fix.
3. **ydotool permissions** — without `ydotoold` + uinput, `--act` can’t click. Dry-run still works.
4. **Wayland security** — some portals block capture; `grim` must be allowed for your session.

## Sanity check

```bash
hyprctl -j activewindow | head
grim /tmp/t.png && file /tmp/t.png
CLICKER_PLATFORM=hyprland uv run python -c "from typesafe_computer_use.platform import load; print(load().platform_info())"
```

## Upstream

Decision logic and action set are Aaron Levin’s ([awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use)). This fork adds the Linux/Hyprland adapter, tesseract OCR path, and NixOS-oriented install notes.
