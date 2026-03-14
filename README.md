# VoxType Tray

A lightweight PyQt6 system tray application for [VoxType](https://github.com/peteonrails/voxtype) — the push-to-talk voice-to-text tool for Linux.

VoxType ships as a CLI daemon with a TOML config file. This app adds a proper GUI: a system tray icon with live status, quick controls, and a full settings editor.

![Python](https://img.shields.io/badge/python-3.11+-blue) ![PyQt6](https://img.shields.io/badge/PyQt6-required-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Features

- **System tray icon** with color-coded state (green=idle, red=recording, amber=transcribing, gray=stopped)
- **Left-click** to toggle recording
- **Right-click menu** for daemon control (start/stop/restart), settings, and recording toggle
- **Full settings editor** with tabs for General, Audio, Whisper, Output, and Hotkey configuration
- **Model management** — download Whisper models directly from the GUI
- **Daemon control** — start, stop, and restart the systemd user service
- **Auto-saves and restarts** the daemon when settings change
- **Hides to tray** on window close — stays running in background

## Why This Exists

VoxType is excellent but configuration-only via a TOML file, with no GUI. On KDE Plasma Wayland specifically:

- `wtype` silently fails (KDE doesn't support the virtual-keyboard Wayland protocol) — you need `dotool` instead
- The evdev hotkey system requires `input` group membership, which isn't obvious
- There's no visual feedback for VoxType's state beyond terminal output

This tray app solves all of that with a native-feeling KDE experience.

## Requirements

- **VoxType** 0.6+ installed and configured
- **Python** 3.11+
- **PyQt6** (`pacman -S python-pyqt6` on Arch)
- **dotool** (for KDE Plasma Wayland — `wtype` won't work)
- **wl-clipboard** (for clipboard fallback)

## Installation

### Quick Install

```bash
# Copy the script
cp voxtype-tray.py ~/.local/bin/voxtype-tray
chmod +x ~/.local/bin/voxtype-tray

# Create desktop entry
cp voxtype-tray.desktop ~/.local/share/applications/

# Autostart on login
cp voxtype-tray.desktop ~/.config/autostart/
```

### KDE Plasma Wayland Setup

If you're on KDE Plasma with Wayland, make sure you have the right dependencies:

```bash
# dotool for text input (wtype does NOT work on KDE Wayland)
yay -S dotool

# Clipboard support
sudo pacman -S wl-clipboard

# Input group for evdev hotkey access
sudo usermod -aG input $USER
# Log out and back in for group change to take effect
```

## Usage

```bash
# Launch (stays in tray)
voxtype-tray

# Or run directly
python3 voxtype-tray.py
```

- **Left-click** the tray icon to toggle recording
- **Right-click** for the full menu
- **Settings** opens the configuration editor
- **Save & Apply** writes the config and restarts the daemon automatically

## Configuration

The app reads and writes `~/.config/voxtype/config.toml`. All settings from VoxType's config are exposed in the GUI:

| Tab | Settings |
|-----|----------|
| **General** | Icon theme, spoken punctuation, word replacements, model downloads |
| **Audio** | Input device, sample rate, max duration, feedback sounds |
| **Whisper** | Model selection, language, threads, GPU isolation, context optimization |
| **Output** | Output mode (type/clipboard/paste), delays, notifications, post-processing |
| **Hotkey** | Enable/disable, key selection, mode (toggle/push-to-talk) |

## Recommended Settings for NVIDIA GPUs

If you have an NVIDIA GPU with Vulkan support:

```bash
# Enable GPU acceleration
sudo voxtype setup gpu --enable

# Use small.en or medium.en for best speed/accuracy
# (configurable in the GUI under Whisper tab)
```

For laptops with hybrid graphics, enable "GPU memory isolation" in the Whisper tab to let the dGPU sleep between transcriptions.

## License

MIT
