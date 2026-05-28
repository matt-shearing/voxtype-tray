#!/usr/bin/env python3
"""VoxType Tray - System tray app with settings for VoxType voice dictation."""

import subprocess
import sys
import os
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QFont, QAction, QPainter, QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QPushButton, QGroupBox, QFormLayout, QLineEdit, QSlider, QMessageBox,
    QFrame, QSystemTrayIcon, QMenu, QStyle,
)

CONFIG_PATH = Path.home() / ".config" / "voxtype" / "config.toml"
DEFAULT_CONFIG_PATH = Path("/etc/voxtype/config.toml")
MODELS_DIR = Path.home() / ".local" / "share" / "voxtype" / "models"
STATE_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "voxtype" / "state"

WHISPER_MODELS = [
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v3", "large-v3-turbo",
]

# Parakeet models per upstream v0.7.x release notes
PARAKEET_MODELS = [
    "parakeet-tdt-0.6b-v3",
    "parakeet-tdt-0.6b-v3-int8",
    "parakeet-tdt-0.6b-v2",
    "parakeet-tdt-0.6b-v2-int8",
]

# Engines accepted by `voxtype --engine` as of 0.7.x
ENGINES = [
    "whisper", "parakeet", "moonshine",
    "sensevoice", "paraformer", "dolphin", "omnilingual", "cohere",
]

HOTKEYS = [
    "SCROLLLOCK", "PAUSE",
    "RIGHTALT", "RIGHTCTRL", "RIGHTSHIFT", "RIGHTMETA",
    "LEFTCTRL", "LEFTSHIFT", "LEFTMETA",
    "F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20",
    "F21", "F22", "F23", "F24",
]

ICON_THEMES = [
    "emoji", "nerd-font", "material", "phosphor", "codicons",
    "omarchy", "minimal", "dots", "arrows", "text",
]

OUTPUT_MODES = ["type", "clipboard", "paste"]
HOTKEY_MODES = ["toggle", "push_to_talk"]
AUDIO_THEMES = ["default", "subtle", "mechanical"]


def read_config():
    path = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Merge overrides into base, preserving keys in base that aren't in overrides."""
    merged = base.copy()
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def write_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read existing config to preserve keys the GUI doesn't manage
    existing = read_config()
    merged = _deep_merge(existing, config)

    lines = []
    lines.append("# VoxType Configuration")
    lines.append("# Managed by voxtype-settings GUI\n")

    def write_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        elif isinstance(v, int):
            return str(v)
        elif isinstance(v, float):
            return str(v)
        elif isinstance(v, str):
            return f'"{v}"'
        elif isinstance(v, list):
            items = ", ".join(write_value(i) for i in v)
            return f"[{items}]"
        elif isinstance(v, dict):
            items = ", ".join(f'"{k}" = {write_value(val)}' for k, val in v.items())
            return f"{{ {items} }}"
        return str(v)

    def write_section(data: dict, prefix=""):
        """Recursively write TOML sections, handling nested tables."""
        top_level = {}
        sub_sections = {}
        for key, val in data.items():
            if isinstance(val, dict) and any(isinstance(v, dict) for v in val.values()):
                # Contains nested dicts — must be a table section
                sub_sections[key] = val
            elif isinstance(val, dict) and val:
                # Flat dict — check if it has enough entries to warrant a section
                # or if keys need quoting (like replacements with spaces)
                has_complex_keys = any(" " in k for k in val)
                if has_complex_keys or len(val) > 3:
                    sub_sections[key] = val
                else:
                    sub_sections[key] = val
            elif isinstance(val, dict):
                pass  # empty dict, skip
            else:
                top_level[key] = val

        def format_key(k):
            """Quote TOML keys that contain spaces or special characters."""
            if " " in k or not k.replace("-", "").replace("_", "").isalnum():
                return f'"{k}"'
            return k

        for key, val in top_level.items():
            lines.append(f"{format_key(key)} = {write_value(val)}")

        for key, val in sub_sections.items():
            section_name = f"{prefix}.{key}" if prefix else key
            # Separate sub-table values from nested sub-tables
            leaf_vals = {}
            nested = {}
            for k, v in val.items():
                if isinstance(v, dict):
                    nested[k] = v
                else:
                    leaf_vals[k] = v
            if leaf_vals:
                lines.append(f"\n[{section_name}]")
                for k, v in leaf_vals.items():
                    lines.append(f"{format_key(k)} = {write_value(v)}")
            for k, v in nested.items():
                nested_name = f"{section_name}.{k}"
                lines.append(f"\n[{nested_name}]")
                write_section(v, nested_name)

    write_section(merged)

    with open(CONFIG_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def check_voxtype_health() -> list[str]:
    """Check for common VoxType misconfigurations. Returns list of warnings."""
    warnings = []

    # Check if voxtype binary points to vulkan backend
    voxtype_bin = Path("/usr/bin/voxtype")
    if voxtype_bin.exists():
        resolved = voxtype_bin.resolve()
        # AMD users on VoxType 0.7.0+ should use the MIGraphX binary,
        # which replaced the old rocm binary. The compatibility symlink
        # `voxtype-onnx-rocm` is documented as one-release-only.
        if "rocm" in resolved.name:
            warnings.append(
                "VoxType binary is the legacy ROCm build. As of 0.7.0 this was\n"
                "renamed to MIGraphX and requires ROCm 7.x.\n"
                "Fix: sudo ln -sf /usr/lib/voxtype/voxtype-onnx-migraphx /usr/bin/voxtype"
            )
        elif "native" in resolved.name or "cpu" in resolved.name:
            warnings.append(
                "VoxType is using CPU backend instead of Vulkan GPU.\n"
                "Fix: sudo ln -sf /usr/lib/voxtype/voxtype-vulkan /usr/bin/voxtype"
            )

    # Check if transcribe-worker is available
    config = read_config()
    gpu_isolation = config.get("whisper", {}).get("gpu_isolation", True)
    if gpu_isolation:
        import shutil
        if not shutil.which("transcribe-worker"):
            warnings.append(
                "GPU isolation is enabled but 'transcribe-worker' not found in PATH.\n"
                "Fix: sudo ln -s /usr/bin/voxtype /usr/local/bin/transcribe-worker"
            )

    # Streaming requires toggle mode (push-to-talk is incompatible per VoxType 0.7.2 notes)
    parakeet_streaming = config.get("parakeet", {}).get("streaming", False)
    hotkey_mode = config.get("hotkey", {}).get("mode", "toggle")
    if parakeet_streaming and hotkey_mode == "push_to_talk":
        warnings.append(
            "Parakeet streaming is enabled but hotkey mode is push_to_talk.\n"
            "Streaming requires toggle activation. Switch hotkey mode to 'toggle'."
        )

    # Streaming only works with the parakeet engine
    if parakeet_streaming and config.get("engine", "whisper") != "parakeet":
        warnings.append(
            "Parakeet streaming is enabled but engine is not 'parakeet'.\n"
            "Set engine to 'parakeet' on the Engine tab, or disable streaming."
        )

    # MIGraphX/CUDA provider lookup failure (upstream voxtype#443).
    # Detect from recent log entries and offer the in-GUI wrapper fix.
    if (backend_supports_onnx(get_current_backend())
            and not voxtype_bin_is_wrapper()
            and provider_lookup_failed_recently()):
        warnings.append(
            "ONNX provider failed to load — VoxType is silently running on CPU.\n"
            "This is upstream voxtype#443 (argv[0]-based .so lookup).\n"
            "Fix: click 'Fix MIGraphX library path' on the General tab,\n"
            "which installs a wrapper script at /usr/bin/voxtype."
        )

    # Parakeet streaming requires tokenizer.model (upstream voxtype#442).
    # Without it the daemon fails to start; with a mismatched one it crashes
    # at first chunk with a Gather-node out-of-range error.
    parakeet_model_name = config.get("parakeet", {}).get("model", "")
    if parakeet_streaming and parakeet_model_name:
        model_dir = (
            Path.home() / ".local/share/voxtype/models" / parakeet_model_name
        )
        tokenizer = model_dir / "tokenizer.model"
        if model_dir.is_dir() and not tokenizer.exists():
            warnings.append(
                f"Parakeet streaming is enabled but {tokenizer} is missing.\n"
                "Upstream voxtype#442 — the model downloader doesn't fetch it.\n"
                "Workaround: set [parakeet] streaming = false until upstream ships a fix."
            )

    # Check GPU device environment in systemd drop-in
    gpu_conf = Path.home() / ".config/systemd/user/voxtype.service.d/gpu.conf"
    if gpu_conf.exists():
        content = gpu_conf.read_text()
        if "VOXTYPE_VULKAN_DEVICE" in content:
            # Check if an NVIDIA GPU is present but config says amd/intel
            try:
                lspci = subprocess.run(
                    ["lspci"], capture_output=True, text=True, timeout=5
                )
                has_nvidia = "nvidia" in lspci.stdout.lower()
                if has_nvidia and "nvidia" not in content.lower():
                    warnings.append(
                        "NVIDIA GPU detected but VOXTYPE_VULKAN_DEVICE is not set to 'nvidia'.\n"
                        f"Check: {gpu_conf}"
                    )
            except Exception:
                pass

    return warnings


def get_daemon_status():
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "voxtype"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


# Human-readable description for each shipped voxtype-* daemon binary.
# These strings are shown in the Backend group of the General tab so users
# understand what they're switching between without consulting docs.
BACKEND_DESCRIPTIONS = {
    "voxtype-avx2":          ("CPU (AVX2)",        "Whisper only"),
    "voxtype-avx512":        ("CPU (AVX-512)",     "Whisper only"),
    "voxtype-vulkan":        ("GPU (Vulkan)",      "Whisper only"),
    "voxtype-onnx-avx2":     ("CPU (AVX2)",        "All ONNX engines (Parakeet, Moonshine, etc.)"),
    "voxtype-onnx-avx512":   ("CPU (AVX-512)",     "All ONNX engines (Parakeet, Moonshine, etc.)"),
    "voxtype-onnx-cuda":     ("NVIDIA CUDA",       "All ONNX engines"),
    "voxtype-onnx-cuda-12":  ("NVIDIA CUDA 12",    "All ONNX engines"),
    "voxtype-onnx-cuda-13":  ("NVIDIA CUDA 13",    "All ONNX engines"),
    "voxtype-onnx-migraphx": ("AMD GPU (MIGraphX)", "All ONNX engines (Parakeet, Moonshine, etc.)"),
    "voxtype-onnx-rocm":     ("AMD GPU (legacy ROCm)", "All ONNX engines — deprecated, use migraphx"),
}


def get_current_backend() -> str:
    """Return the basename of the binary /usr/bin/voxtype currently links to."""
    bin_path = Path("/usr/bin/voxtype")
    if not bin_path.exists():
        return "unknown"
    try:
        return bin_path.resolve().name
    except Exception:
        return "unknown"


def backend_supports_onnx(name: str) -> bool:
    return "onnx" in name


def voxtype_bin_is_wrapper() -> bool:
    """True when /usr/bin/voxtype is a shell script (the workaround for upstream
    voxtype#443), False when it's a direct symlink to a binary."""
    bin_path = Path("/usr/bin/voxtype")
    if not bin_path.exists():
        return False
    try:
        with open(bin_path, "rb") as f:
            head = f.read(4)
        return head.startswith(b"#!")
    except Exception:
        return False


# Wrapper script content. Installed in place of /usr/bin/voxtype to work around
# the argv[0]-based provider-library lookup in ORT (see voxtype upstream #443).
# Probes the installed variants in order: GPU-first (MIGraphX, CUDA, Vulkan),
# then CPU ONNX, then CPU Whisper. Always exec's the real binary so argv[0]
# resolves into the binary's own directory, where the .so files live.
WRAPPER_SCRIPT = """#!/bin/sh
# Installed by voxtype-tray as a workaround for voxtype upstream #443.
# Re-execs the real variant binary so ORT's argv[0]-based provider lookup
# finds libonnxruntime_providers_*.so in the same directory.
target="$(readlink -f /usr/lib/voxtype/voxtype-onnx-migraphx 2>/dev/null \\
  || readlink -f /usr/lib/voxtype/voxtype-onnx-cuda-13 2>/dev/null \\
  || readlink -f /usr/lib/voxtype/voxtype-onnx-cuda-12 2>/dev/null \\
  || readlink -f /usr/lib/voxtype/voxtype-vulkan 2>/dev/null \\
  || readlink -f /usr/lib/voxtype/voxtype-onnx-avx512 2>/dev/null \\
  || readlink -f /usr/lib/voxtype/voxtype-avx512 2>/dev/null)"
if [ -z "$target" ] || [ ! -x "$target" ]; then
  echo "voxtype-wrapper: no voxtype variant found under /usr/lib/voxtype/" >&2
  exit 1
fi
exec "$target" "$@"
"""


def provider_lookup_failed_recently() -> bool:
    """Scan recent voxtype journal entries for the ORT provider-lookup failure.
    This is the signature of upstream voxtype#443 — the daemon falls back to CPU
    silently, so we have to look for the log entry rather than process state."""
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "voxtype",
             "--since", "10 minutes ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    return "libonnxruntime_providers_shared.so" in result.stdout


def detect_gpu_vendor() -> str:
    """Return 'amd', 'nvidia', 'intel', or 'unknown'."""
    try:
        out = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5
        ).stdout.lower()
        if "nvidia" in out:
            return "nvidia"
        if "amd/ati" in out or "radeon" in out:
            return "amd"
        if "intel" in out and ("vga" in out or "graphics" in out):
            return "intel"
    except Exception:
        pass
    return "unknown"


def get_voxtype_state():
    """Read the current VoxType state (idle/recording/transcribing/stopped)."""
    try:
        if STATE_FILE.exists():
            return STATE_FILE.read_text().strip()
    except Exception:
        pass
    return "stopped" if not get_daemon_status() else "idle"


def get_installed_models():
    if not MODELS_DIR.exists():
        return []
    models = []
    for item in MODELS_DIR.iterdir():
        if item.is_file() and item.suffix == ".bin":
            name = item.stem
            if name.startswith("ggml-"):
                name = name[5:]
            models.append(name)
        elif item.is_dir():
            models.append(item.name)
    return models


def make_tray_icon(state: str) -> QIcon:
    """Create a colored circle icon based on VoxType state."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    colors = {
        "idle": QColor(100, 180, 100),       # green - ready
        "recording": QColor(220, 50, 50),    # red - recording
        "transcribing": QColor(255, 180, 0), # amber - processing
        "stopped": QColor(128, 128, 128),    # gray - stopped
    }
    color = colors.get(state, colors["stopped"])

    # Outer ring
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color.darker(130))
    painter.drawEllipse(2, 2, size - 4, size - 4)

    # Inner circle
    painter.setBrush(color)
    painter.drawEllipse(6, 6, size - 12, size - 12)

    # Highlight
    painter.setBrush(QColor(255, 255, 255, 60))
    painter.drawEllipse(14, 10, size - 32, size // 3)

    painter.end()
    return QIcon(pixmap)


class VoxTypeTray(QSystemTrayIcon):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings_window = None
        self._current_state = ""

        self.update_icon()

        # Context menu — parent all QActions to menu so they survive
        # DBusMenu serialization on KDE Plasma Wayland
        self._menu = QMenu()

        self.status_action = self._menu.addAction("VoxType: checking...")
        self.status_action.setEnabled(False)

        self._menu.addSeparator()

        self.toggle_record_action = self._menu.addAction("Toggle Recording")
        self.toggle_record_action.triggered.connect(self.toggle_recording)

        self.daemon_action = self._menu.addAction("Start Daemon")
        self.daemon_action.triggered.connect(self.toggle_daemon)

        self.restart_action = self._menu.addAction("Restart Daemon")
        self.restart_action.triggered.connect(self.restart_daemon)

        self._menu.addSeparator()

        self.settings_action = self._menu.addAction("Settings...")
        self.settings_action.triggered.connect(self.show_settings)

        self.quit_action = self._menu.addAction("Quit Tray")
        self.quit_action.triggered.connect(self.quit_app)

        self.setContextMenu(self._menu)

        # Left-click toggles recording
        self.activated.connect(self.on_activated)

        # Poll state every 2 seconds
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start(2000)

        self.refresh_state()
        self.show()

        # Run health check on startup
        self._run_health_check()

    def _run_health_check(self):
        warnings = check_voxtype_health()
        if warnings:
            msg = "VoxType configuration issues detected:\n\n" + "\n\n".join(warnings)
            self.showMessage(
                "VoxType Health Check",
                msg,
                QSystemTrayIcon.MessageIcon.Warning,
                10000,
            )

    def update_icon(self, state="stopped"):
        if state != self._current_state:
            self._current_state = state
            self.setIcon(make_tray_icon(state))

    def refresh_state(self):
        state = get_voxtype_state()
        self.update_icon(state)

        running = get_daemon_status()
        if running:
            state_labels = {
                "idle": "Idle - Ready",
                "recording": "Recording...",
                "transcribing": "Transcribing...",
            }
            label = state_labels.get(state, state.capitalize())
            self.status_action.setText(f"VoxType: {label}")
            self.daemon_action.setText("Stop Daemon")
            self.toggle_record_action.setEnabled(True)
            self.setToolTip(f"VoxType - {label}")
        else:
            self.status_action.setText("VoxType: Stopped")
            self.daemon_action.setText("Start Daemon")
            self.toggle_record_action.setEnabled(False)
            self.setToolTip("VoxType - Stopped")

    def on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left click - toggle recording
            if get_daemon_status():
                self.toggle_recording()
            else:
                self.show_settings()

    def toggle_recording(self):
        subprocess.Popen(
            ["voxtype", "record", "toggle"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        QTimer.singleShot(500, self.refresh_state)

    def toggle_daemon(self):
        if get_daemon_status():
            subprocess.run(["systemctl", "--user", "stop", "voxtype"], timeout=10)
        else:
            self._run_health_check()
            subprocess.run(["systemctl", "--user", "start", "voxtype"], timeout=10)
        QTimer.singleShot(1500, self.refresh_state)

    def restart_daemon(self):
        self._run_health_check()
        subprocess.run(["systemctl", "--user", "restart", "voxtype"], timeout=10)
        QTimer.singleShot(1500, self.refresh_state)

    def show_settings(self):
        if self.settings_window is None or not self.settings_window.isVisible():
            self.settings_window = VoxTypeSettings(self)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def quit_app(self):
        self.hide()
        self.app.quit()


class VoxTypeSettings(QMainWindow):
    def __init__(self, tray=None):
        super().__init__()
        self.tray = tray
        self.config = read_config()
        self.setWindowTitle("VoxType Settings")
        self.setWindowIcon(QIcon.fromTheme("audio-input-microphone"))
        self.setMinimumSize(520, 580)
        self.resize(560, 640)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)

        # Header
        header = QHBoxLayout()
        title = QLabel("VoxType Settings")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title)
        header.addStretch()

        self.status_label = QLabel()
        self.status_label.setFont(QFont("monospace", 10))
        header.addWidget(self.status_label)

        self.daemon_btn = QPushButton()
        self.daemon_btn.setFixedWidth(100)
        self.daemon_btn.clicked.connect(self.toggle_daemon)
        header.addWidget(self.daemon_btn)

        layout.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_audio_tab(), "Audio")
        tabs.addTab(self._build_engine_tab(), "Engine")
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_hotkey_tab(), "Hotkey")
        layout.addWidget(tabs)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save && Apply")
        save_btn.setFixedWidth(130)
        save_btn.clicked.connect(self.save_config)
        btn_layout.addWidget(save_btn)

        reload_btn = QPushButton("Reload")
        reload_btn.setFixedWidth(80)
        reload_btn.clicked.connect(self.reload_config)
        btn_layout.addWidget(reload_btn)

        layout.addLayout(btn_layout)

        self.statusBar().showMessage("Ready")

        self.refresh_status()
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(5000)

    def _get(self, *keys, default=None):
        data = self.config
        for k in keys:
            if isinstance(data, dict):
                data = data.get(k, default)
            else:
                return default
        return data if data is not None else default

    def _build_general_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox("Display")
        form = QFormLayout(group)
        self.icon_theme = QComboBox()
        self.icon_theme.addItems(ICON_THEMES)
        current = self._get("status", "icon_theme", default="emoji")
        idx = ICON_THEMES.index(current) if current in ICON_THEMES else 0
        self.icon_theme.setCurrentIndex(idx)
        form.addRow("Icon Theme:", self.icon_theme)
        layout.addWidget(group)

        group2 = QGroupBox("Text Processing")
        form2 = QFormLayout(group2)
        self.spoken_punct = QCheckBox("Enable spoken punctuation")
        self.spoken_punct.setChecked(self._get("text", "spoken_punctuation", default=False))
        form2.addRow(self.spoken_punct)

        self.replacements_edit = QLineEdit()
        replacements = self._get("text", "replacements", default={})
        self.replacements_edit.setText(
            ", ".join(f"{k}={v}" for k, v in replacements.items()) if replacements else ""
        )
        self.replacements_edit.setPlaceholderText('word=replacement, other=value')
        form2.addRow("Replacements:", self.replacements_edit)
        layout.addWidget(group2)

        # Backend management — added v1.2.1 so users don't need a terminal
        # to enable GPU acceleration or switch to the ONNX binary required
        # for Parakeet/streaming. Both buttons elevate via pkexec.
        backend_group = QGroupBox("VoxType Backend")
        bv = QVBoxLayout(backend_group)
        self.backend_status_label = QLabel()
        self.backend_status_label.setWordWrap(True)
        bv.addWidget(self.backend_status_label)

        btn_row = QHBoxLayout()
        self.enable_gpu_btn = QPushButton("Enable GPU acceleration")
        self.enable_gpu_btn.setToolTip(
            "Runs 'voxtype setup gpu --enable' (auto-picks Vulkan/CUDA/MIGraphX for your hardware). Requires authentication."
        )
        self.enable_gpu_btn.clicked.connect(self.enable_gpu)
        btn_row.addWidget(self.enable_gpu_btn)

        self.enable_onnx_btn = QPushButton("Enable ONNX (Parakeet, etc.)")
        self.enable_onnx_btn.setToolTip(
            "Runs 'voxtype setup onnx --enable' to switch to the ONNX binary, unlocking Parakeet streaming. Requires authentication."
        )
        self.enable_onnx_btn.clicked.connect(self.enable_onnx)
        btn_row.addWidget(self.enable_onnx_btn)
        bv.addLayout(btn_row)

        # Wrapper-fix button — hidden unless needed (upstream voxtype#443).
        # Visibility is controlled by _refresh_backend_status() so the button
        # only appears when we detect the provider-lookup failure or when the
        # /usr/bin/voxtype symlink would benefit from being replaced.
        self.fix_migraphx_btn = QPushButton("Fix MIGraphX library path (voxtype#443)")
        self.fix_migraphx_btn.setToolTip(
            "Replaces the /usr/bin/voxtype symlink with a wrapper script so ORT can "
            "find its provider libraries. Workaround for upstream voxtype#443. "
            "Requires authentication."
        )
        self.fix_migraphx_btn.clicked.connect(self.fix_migraphx_wrapper)
        bv.addWidget(self.fix_migraphx_btn)
        layout.addWidget(backend_group)
        self._refresh_backend_status()

        group3 = QGroupBox("Models")
        vl = QVBoxLayout(group3)
        installed = get_installed_models()
        self.models_label = QLabel(
            f"Installed: {', '.join(installed)}" if installed else "No models installed"
        )
        self.models_label.setWordWrap(True)
        vl.addWidget(self.models_label)

        dl_layout = QHBoxLayout()
        self.dl_model_combo = QComboBox()
        self.dl_model_combo.addItems(WHISPER_MODELS + PARAKEET_MODELS)
        dl_layout.addWidget(self.dl_model_combo)
        dl_btn = QPushButton("Download Model")
        dl_btn.clicked.connect(self.download_model)
        dl_layout.addWidget(dl_btn)
        vl.addLayout(dl_layout)
        layout.addWidget(group3)

        layout.addStretch()
        return w

    def _refresh_backend_status(self):
        current = get_current_backend()
        backend_label, engines = BACKEND_DESCRIPTIONS.get(
            current, (current, "Unknown binary")
        )
        gpu = detect_gpu_vendor()
        is_wrapper = voxtype_bin_is_wrapper()
        provider_broken = (
            backend_supports_onnx(current)
            and not is_wrapper
            and provider_lookup_failed_recently()
        )

        recommendation = ""
        if provider_broken:
            recommendation = (
                "<br><span style='color:#d94545;'>ONNX provider library "
                "lookup is failing — daemon is silently using CPU fallback. "
                "Click 'Fix MIGraphX library path' below.</span>"
            )
        elif is_wrapper:
            recommendation = (
                "<br><span style='color:#4caf50;'>Wrapper script installed "
                "(voxtype#443 workaround active).</span>"
            )
        elif gpu == "amd" and current != "voxtype-onnx-migraphx":
            recommendation = (
                "<br><span style='color:#b87333;'>AMD GPU detected — "
                "switch to voxtype-onnx-migraphx (Enable GPU + Enable ONNX) "
                "for Parakeet streaming with GPU acceleration.</span>"
            )
        elif gpu == "nvidia" and not current.startswith("voxtype-onnx-cuda"):
            recommendation = (
                "<br><span style='color:#b87333;'>NVIDIA GPU detected — "
                "click 'Enable GPU' then 'Enable ONNX' for full acceleration.</span>"
            )
        self.backend_status_label.setText(
            f"<b>Current:</b> {current}<br>"
            f"<b>Mode:</b> {backend_label} — {engines}{recommendation}"
        )
        # Only surface the wrapper-install button when it would actually help.
        self.fix_migraphx_btn.setVisible(provider_broken or (
            backend_supports_onnx(current) and not is_wrapper
        ))

    def _run_pkexec(self, args: list[str], success_msg: str):
        """Run a privileged voxtype subcommand via pkexec, then restart daemon."""
        try:
            proc = subprocess.run(
                ["pkexec"] + args,
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            QMessageBox.critical(
                self, "pkexec not found",
                "polkit's pkexec is required to switch backends.\n"
                "Install polkit, or run the command manually in a terminal:\n"
                f"  sudo {' '.join(args)}"
            )
            return False
        except subprocess.TimeoutExpired:
            QMessageBox.critical(self, "Timeout", "Backend switch timed out.")
            return False

        if proc.returncode == 0:
            self.statusBar().showMessage(success_msg)
            self._refresh_backend_status()
            if get_daemon_status():
                subprocess.run(
                    ["systemctl", "--user", "restart", "voxtype"], timeout=10
                )
            return True

        # pkexec returns 126 when the user cancels the auth dialog
        if proc.returncode == 126:
            self.statusBar().showMessage("Backend switch cancelled")
            return False
        QMessageBox.critical(
            self, "Backend switch failed",
            f"Exit code {proc.returncode}\n\n{proc.stderr or proc.stdout}"
        )
        return False

    def enable_gpu(self):
        self._run_pkexec(
            ["voxtype", "setup", "gpu", "--enable"],
            "GPU acceleration enabled"
        )

    def enable_onnx(self):
        self._run_pkexec(
            ["voxtype", "setup", "onnx", "--enable"],
            "ONNX engines enabled (Parakeet now available)"
        )

    def fix_migraphx_wrapper(self):
        """Install a wrapper script at /usr/bin/voxtype to work around
        upstream voxtype#443 (ORT provider lookup uses argv[0])."""
        import tempfile
        confirm = QMessageBox.question(
            self, "Install voxtype wrapper?",
            "This replaces /usr/bin/voxtype with a small shell wrapper that exec's "
            "the real binary directly. It's a workaround for upstream voxtype#443 — "
            "without it, ONNX provider libraries fail to load and the daemon falls "
            "back to CPU silently.\n\n"
            "The wrapper auto-detects which variant is installed (MIGraphX, CUDA, "
            "Vulkan, etc.) so it survives future 'voxtype setup gpu' changes.\n\n"
            "Continue? (Requires admin authentication.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Write wrapper to a temp file that pkexec can read as root
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, prefix="voxtype-wrapper-"
        ) as tmp:
            tmp.write(WRAPPER_SCRIPT)
            tmp_path = tmp.name

        try:
            ok = self._run_pkexec(
                ["install", "-m755", tmp_path, "/usr/bin/voxtype"],
                "Wrapper installed (voxtype#443 workaround active)",
            )
            if ok:
                # Force-restart daemon so it picks up the new entry point
                if get_daemon_status():
                    subprocess.run(
                        ["systemctl", "--user", "restart", "voxtype"], timeout=10
                    )
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    def _build_audio_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox("Input")
        form = QFormLayout(group)
        self.audio_device = QLineEdit(self._get("audio", "device", default="default"))
        form.addRow("Device:", self.audio_device)

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setSingleStep(8000)
        self.sample_rate.setValue(self._get("audio", "sample_rate", default=16000))
        form.addRow("Sample Rate:", self.sample_rate)

        self.max_duration = QSpinBox()
        self.max_duration.setRange(10, 600)
        self.max_duration.setValue(self._get("audio", "max_duration_secs", default=120))
        self.max_duration.setSuffix(" sec")
        form.addRow("Max Duration:", self.max_duration)

        self.pause_media = QCheckBox("Pause media players while recording")
        self.pause_media.setChecked(self._get("audio", "pause_media", default=False))
        self.pause_media.setToolTip("MPRIS players (Spotify, mpv, browsers) pause during recording (v0.6.6+)")
        form.addRow(self.pause_media)
        layout.addWidget(group)

        group2 = QGroupBox("Feedback")
        form2 = QFormLayout(group2)
        self.feedback_enabled = QCheckBox("Enable audio feedback")
        self.feedback_enabled.setChecked(self._get("audio", "feedback", "enabled", default=True))
        form2.addRow(self.feedback_enabled)

        self.feedback_theme = QComboBox()
        self.feedback_theme.addItems(AUDIO_THEMES)
        current = self._get("audio", "feedback", "theme", default="subtle")
        idx = AUDIO_THEMES.index(current) if current in AUDIO_THEMES else 0
        self.feedback_theme.setCurrentIndex(idx)
        form2.addRow("Theme:", self.feedback_theme)

        self.feedback_volume = QSlider(Qt.Orientation.Horizontal)
        self.feedback_volume.setRange(0, 100)
        vol = self._get("audio", "feedback", "volume", default=0.5)
        self.feedback_volume.setValue(int(vol * 100))
        form2.addRow("Volume:", self.feedback_volume)
        layout.addWidget(group2)

        layout.addStretch()
        return w

    def _build_engine_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        # Top-level engine selector — added in v1.2.0 to support Parakeet & streaming
        engine_group = QGroupBox("Engine")
        engine_form = QFormLayout(engine_group)
        self.engine = QComboBox()
        self.engine.addItems(ENGINES)
        current_engine = self._get("engine", default="whisper")
        if current_engine in ENGINES:
            self.engine.setCurrentIndex(ENGINES.index(current_engine))
        else:
            self.engine.setCurrentIndex(0)
        self.engine.currentTextChanged.connect(self._update_engine_visibility)
        engine_form.addRow("Active engine:", self.engine)
        engine_hint = QLabel(
            "Whisper: general-purpose, many languages.  "
            "Parakeet: fastest, supports live streaming (English)."
        )
        engine_hint.setStyleSheet("color: gray; font-size: 11px;")
        engine_hint.setWordWrap(True)
        engine_form.addRow(engine_hint)
        layout.addWidget(engine_group)

        group = QGroupBox("Whisper")
        self.whisper_group = group
        form = QFormLayout(group)
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(WHISPER_MODELS)
        current = self._get("whisper", "model", default="small.en")
        idx = WHISPER_MODELS.index(current) if current in WHISPER_MODELS else 0
        self.whisper_model.setCurrentIndex(idx)
        form.addRow("Model:", self.whisper_model)

        self.whisper_lang = QLineEdit(self._get("whisper", "language", default="en"))
        self.whisper_lang.setMaximumWidth(80)
        form.addRow("Language:", self.whisper_lang)

        self.whisper_translate = QCheckBox("Translate to English")
        self.whisper_translate.setChecked(self._get("whisper", "translate", default=False))
        form.addRow(self.whisper_translate)

        self.whisper_threads = QSpinBox()
        self.whisper_threads.setRange(1, 32)
        self.whisper_threads.setValue(self._get("whisper", "threads", default=8))
        form.addRow("CPU Threads:", self.whisper_threads)
        layout.addWidget(group)

        # Parakeet group — live text streaming (v0.7.2+)
        # Kept visible as a teaser when engine=whisper so the streaming
        # feature is discoverable. _update_engine_visibility() greys the
        # body and toggles the hint label below.
        pgroup = QGroupBox("Parakeet (live streaming)")
        self.parakeet_group = pgroup
        pform = QFormLayout(pgroup)

        self.parakeet_hint = QLabel(
            "Switch the engine above to <b>parakeet</b> to enable these settings.\n"
            "Streaming requires a voxtype-onnx-* binary (your current binary "
            "may be Whisper-only — check 'voxtype setup model')."
        )
        self.parakeet_hint.setStyleSheet(
            "color: #b87333; background: #2a1f0f; padding: 6px; border-radius: 4px; font-size: 11px;"
        )
        self.parakeet_hint.setWordWrap(True)
        pform.addRow(self.parakeet_hint)

        self.parakeet_model = QComboBox()
        self.parakeet_model.addItems(PARAKEET_MODELS)
        self.parakeet_model.setEditable(True)
        current = self._get("parakeet", "model", default="parakeet-tdt-0.6b-v3")
        if current in PARAKEET_MODELS:
            self.parakeet_model.setCurrentIndex(PARAKEET_MODELS.index(current))
        else:
            self.parakeet_model.setCurrentText(current)
        pform.addRow("Model:", self.parakeet_model)

        self.parakeet_streaming = QCheckBox("Stream text live as you speak")
        self.parakeet_streaming.setChecked(self._get("parakeet", "streaming", default=False))
        self.parakeet_streaming.setToolTip(
            "Live transcription appears at the cursor as you speak (v0.7.2+).\n"
            "Requires toggle hotkey mode — push-to-talk is incompatible."
        )
        self.parakeet_streaming.toggled.connect(self._update_streaming_visibility)
        pform.addRow(self.parakeet_streaming)

        self.streaming_chunk_secs = QDoubleSpinBox()
        self.streaming_chunk_secs.setRange(0.08, 2.0)
        self.streaming_chunk_secs.setSingleStep(0.04)
        self.streaming_chunk_secs.setDecimals(2)
        self.streaming_chunk_secs.setValue(
            float(self._get("parakeet", "streaming_chunk_secs", default=0.32))
        )
        self.streaming_chunk_secs.setSuffix(" sec")
        self.streaming_chunk_secs.setToolTip("Audio chunk length per streaming step")
        pform.addRow("Chunk size:", self.streaming_chunk_secs)

        self.streaming_left_context = QDoubleSpinBox()
        self.streaming_left_context.setRange(0.0, 20.0)
        self.streaming_left_context.setSingleStep(0.4)
        self.streaming_left_context.setDecimals(2)
        self.streaming_left_context.setValue(
            float(self._get("parakeet", "streaming_left_context_secs", default=5.6))
        )
        self.streaming_left_context.setSuffix(" sec")
        self.streaming_left_context.setToolTip("Past audio kept as context per step")
        pform.addRow("Left context:", self.streaming_left_context)

        self.streaming_right_context = QDoubleSpinBox()
        self.streaming_right_context.setRange(0.0, 2.0)
        self.streaming_right_context.setSingleStep(0.04)
        self.streaming_right_context.setDecimals(2)
        self.streaming_right_context.setValue(
            float(self._get("parakeet", "streaming_right_context_secs", default=0.32))
        )
        self.streaming_right_context.setSuffix(" sec")
        self.streaming_right_context.setToolTip("Lookahead audio per step (lower = lower latency)")
        pform.addRow("Right context:", self.streaming_right_context)

        layout.addWidget(pgroup)

        group2 = QGroupBox("Whisper Performance")
        self.whisper_perf_group = group2
        form2 = QFormLayout(group2)
        self.on_demand = QCheckBox("On-demand model loading")
        self.on_demand.setChecked(self._get("whisper", "on_demand_loading", default=False))
        self.on_demand.setToolTip("Load model only when recording. Saves RAM but adds latency.")
        form2.addRow(self.on_demand)

        self.gpu_isolation = QCheckBox("GPU memory isolation")
        self.gpu_isolation.setChecked(self._get("whisper", "gpu_isolation", default=True))
        self.gpu_isolation.setToolTip("Let dGPU sleep between transcriptions (laptop battery saver)")
        form2.addRow(self.gpu_isolation)

        self.ctx_opt = QCheckBox("Context window optimization")
        self.ctx_opt.setChecked(self._get("whisper", "context_window_optimization", default=True))
        self.ctx_opt.setToolTip("Faster transcription for short clips. Disable if you get repetition.")
        form2.addRow(self.ctx_opt)

        self.gpu_device = QSpinBox()
        self.gpu_device.setRange(-1, 15)
        self.gpu_device.setSpecialValueText("Auto")
        gpu_dev = self._get("whisper", "gpu_device", default=-1)
        self.gpu_device.setValue(gpu_dev if gpu_dev is not None else -1)
        self.gpu_device.setToolTip("GPU device index for multi-GPU systems (-1 = auto)")
        form2.addRow("GPU Device:", self.gpu_device)

        self.eager_processing = QCheckBox("Eager processing (transcribe while recording)")
        self.eager_processing.setChecked(self._get("whisper", "eager_processing", default=False))
        self.eager_processing.setToolTip("Transcribe audio in chunks while still recording")
        form2.addRow(self.eager_processing)

        self.eager_chunk_secs = QDoubleSpinBox()
        self.eager_chunk_secs.setRange(1.0, 30.0)
        self.eager_chunk_secs.setSingleStep(0.5)
        self.eager_chunk_secs.setDecimals(1)
        self.eager_chunk_secs.setValue(float(self._get("whisper", "eager_chunk_secs", default=5.0)))
        self.eager_chunk_secs.setSuffix(" sec")
        form2.addRow("Eager chunk size:", self.eager_chunk_secs)

        self.eager_overlap_secs = QDoubleSpinBox()
        self.eager_overlap_secs.setRange(0.0, 5.0)
        self.eager_overlap_secs.setSingleStep(0.1)
        self.eager_overlap_secs.setDecimals(1)
        self.eager_overlap_secs.setValue(float(self._get("whisper", "eager_overlap_secs", default=0.5)))
        self.eager_overlap_secs.setSuffix(" sec")
        form2.addRow("Eager overlap:", self.eager_overlap_secs)
        layout.addWidget(group2)

        self._update_engine_visibility(self.engine.currentText())
        self._update_streaming_visibility(self.parakeet_streaming.isChecked())

        layout.addStretch()
        return w

    def _update_engine_visibility(self, engine_name: str):
        if not hasattr(self, "whisper_group"):
            return
        is_whisper = engine_name == "whisper"
        is_parakeet = engine_name == "parakeet"
        self.whisper_group.setVisible(is_whisper)
        self.whisper_perf_group.setVisible(is_whisper)

        # Parakeet group stays visible for whisper too (as a teaser) so the
        # streaming feature is discoverable. For other engines (moonshine etc.),
        # hide it since we don't have controls for those engines yet.
        self.parakeet_group.setVisible(is_parakeet or is_whisper)
        self.parakeet_model.setEnabled(is_parakeet)
        self.parakeet_streaming.setEnabled(is_parakeet)
        self.parakeet_hint.setVisible(is_whisper)
        # Streaming spinboxes follow both engine and checkbox state
        self._update_streaming_visibility(self.parakeet_streaming.isChecked())

    def _update_streaming_visibility(self, on: bool):
        if not hasattr(self, "streaming_chunk_secs"):
            return
        # Spinboxes only meaningful when engine=parakeet AND streaming is on
        engine_is_parakeet = self.engine.currentText() == "parakeet"
        active = on and engine_is_parakeet
        for w in (self.streaming_chunk_secs, self.streaming_left_context, self.streaming_right_context):
            w.setEnabled(active)

    def _build_output_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox("Output Mode")
        form = QFormLayout(group)
        self.output_mode = QComboBox()
        self.output_mode.addItems(OUTPUT_MODES)
        current = self._get("output", "mode", default="type")
        idx = OUTPUT_MODES.index(current) if current in OUTPUT_MODES else 0
        self.output_mode.setCurrentIndex(idx)
        form.addRow("Mode:", self.output_mode)

        self.fallback_clip = QCheckBox("Fallback to clipboard")
        self.fallback_clip.setChecked(self._get("output", "fallback_to_clipboard", default=True))
        form.addRow(self.fallback_clip)

        self.type_delay = QSpinBox()
        self.type_delay.setRange(0, 100)
        self.type_delay.setValue(self._get("output", "type_delay_ms", default=0))
        self.type_delay.setSuffix(" ms")
        form.addRow("Type Delay:", self.type_delay)

        self.pre_type_delay = QSpinBox()
        self.pre_type_delay.setRange(0, 500)
        self.pre_type_delay.setValue(self._get("output", "pre_type_delay_ms", default=50))
        self.pre_type_delay.setSuffix(" ms")
        form.addRow("Pre-type Delay:", self.pre_type_delay)

        # Modifier-release guard — defaults on in 0.7.2+, expose for users who
        # need to disable it (e.g. for chord-style hotkeys)
        self.wait_modifier_release = QCheckBox("Wait for modifier-key release before typing")
        self.wait_modifier_release.setChecked(
            self._get("output", "wait_for_modifier_release", default=True)
        )
        self.wait_modifier_release.setToolTip(
            "Prevents your hotkey modifiers from combining with the typed text (v0.7.2+)"
        )
        form.addRow(self.wait_modifier_release)

        self.modifier_release_timeout = QSpinBox()
        self.modifier_release_timeout.setRange(50, 5000)
        self.modifier_release_timeout.setSingleStep(50)
        self.modifier_release_timeout.setValue(
            self._get("output", "modifier_release_timeout_ms", default=750)
        )
        self.modifier_release_timeout.setSuffix(" ms")
        form.addRow("Modifier wait timeout:", self.modifier_release_timeout)
        layout.addWidget(group)

        group_text = QGroupBox("Smart Input")
        form_text = QFormLayout(group_text)
        self.smart_auto_submit = QCheckBox('Say "submit"/"send"/"enter" to auto-press Enter')
        self.smart_auto_submit.setChecked(self._get("text", "smart_auto_submit", default=False))
        self.smart_auto_submit.setToolTip("Trigger word is stripped from output text")
        form_text.addRow(self.smart_auto_submit)
        layout.addWidget(group_text)

        group2 = QGroupBox("Notifications")
        form2 = QFormLayout(group2)
        self.notif_start = QCheckBox("On recording start")
        self.notif_start.setChecked(self._get("output", "notification", "on_recording_start", default=True))
        form2.addRow(self.notif_start)

        self.notif_stop = QCheckBox("On recording stop")
        self.notif_stop.setChecked(self._get("output", "notification", "on_recording_stop", default=False))
        form2.addRow(self.notif_stop)

        self.notif_transcription = QCheckBox("On transcription complete")
        self.notif_transcription.setChecked(self._get("output", "notification", "on_transcription", default=True))
        form2.addRow(self.notif_transcription)
        layout.addWidget(group2)

        group3 = QGroupBox("Post-Processing (Optional)")
        form3 = QFormLayout(group3)
        self.post_cmd = QLineEdit(self._get("output", "post_process", "command", default=""))
        self.post_cmd.setPlaceholderText("e.g., ollama run llama3.2:1b 'Clean up this dictation...'")
        form3.addRow("Command:", self.post_cmd)
        self.post_timeout = QSpinBox()
        self.post_timeout.setRange(1000, 120000)
        self.post_timeout.setValue(self._get("output", "post_process", "timeout_ms", default=30000))
        self.post_timeout.setSuffix(" ms")
        form3.addRow("Timeout:", self.post_timeout)
        layout.addWidget(group3)

        layout.addStretch()
        return w

    def _build_hotkey_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox("Hotkey Configuration")
        form = QFormLayout(group)
        self.hotkey_enabled = QCheckBox("Enable built-in hotkey (evdev)")
        self.hotkey_enabled.setChecked(self._get("hotkey", "enabled", default=True))
        form.addRow(self.hotkey_enabled)

        self.hotkey_key = QComboBox()
        self.hotkey_key.addItems(HOTKEYS)
        self.hotkey_key.setEditable(True)
        current = self._get("hotkey", "key", default="SCROLLLOCK")
        if current in HOTKEYS:
            self.hotkey_key.setCurrentIndex(HOTKEYS.index(current))
        else:
            self.hotkey_key.setCurrentText(current)
        form.addRow("Key:", self.hotkey_key)

        self.hotkey_mode = QComboBox()
        self.hotkey_mode.addItems(HOTKEY_MODES)
        current = self._get("hotkey", "mode", default="toggle")
        idx = HOTKEY_MODES.index(current) if current in HOTKEY_MODES else 0
        self.hotkey_mode.setCurrentIndex(idx)
        form.addRow("Mode:", self.hotkey_mode)

        info = QLabel(
            "ScrollLock is ideal: it's a dedicated key that doesn't conflict\n"
            "with any apps or compositor shortcuts. For push-to-talk,\n"
            "hold it while speaking. For toggle, tap to start/stop."
        )
        info.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(info)
        layout.addWidget(group)

        group2 = QGroupBox("Tips")
        vl = QVBoxLayout(group2)
        tips = QLabel(
            "ScrollLock: Best choice - unused by virtually all apps\n"
            "Pause/Break: Also rarely used, good alternative\n"
            "F13-F24: Available if your keyboard supports them\n"
            "For evdev hotkeys, user must be in the 'input' group\n"
            "Run: sudo usermod -aG input $USER (then log out/in)"
        )
        tips.setStyleSheet("font-size: 11px;")
        vl.addWidget(tips)
        layout.addWidget(group2)

        layout.addStretch()
        return w

    def refresh_status(self):
        running = get_daemon_status()
        if running:
            self.status_label.setText("Running")
            self.status_label.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.daemon_btn.setText("Stop")
        else:
            self.status_label.setText("Stopped")
            self.status_label.setStyleSheet("color: #f44336; font-weight: bold;")
            self.daemon_btn.setText("Start")

    def toggle_daemon(self):
        if get_daemon_status():
            subprocess.run(["systemctl", "--user", "stop", "voxtype"], timeout=10)
            self.statusBar().showMessage("Daemon stopped")
        else:
            self._warn_health_issues()
            subprocess.run(["systemctl", "--user", "start", "voxtype"], timeout=10)
            self.statusBar().showMessage("Daemon started")
        QTimer.singleShot(1000, self.refresh_status)

    def _warn_health_issues(self):
        warnings = check_voxtype_health()
        if warnings:
            msg = "Configuration issues detected:\n\n" + "\n\n".join(warnings)
            QMessageBox.warning(self, "VoxType Health Check", msg)

    def save_config(self):
        replacements = {}
        repl_text = self.replacements_edit.text().strip()
        if repl_text:
            for pair in repl_text.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    replacements[k.strip()] = v.strip()

        config = {
            "engine": self.engine.currentText(),
            "state_file": "auto",
            "hotkey": {
                "enabled": self.hotkey_enabled.isChecked(),
                "key": self.hotkey_key.currentText(),
                "modifiers": [],
                "mode": self.hotkey_mode.currentText(),
            },
            "audio": {
                "device": self.audio_device.text(),
                "sample_rate": self.sample_rate.value(),
                "max_duration_secs": self.max_duration.value(),
                "pause_media": self.pause_media.isChecked(),
                "feedback": {
                    "enabled": self.feedback_enabled.isChecked(),
                    "theme": self.feedback_theme.currentText(),
                    "volume": self.feedback_volume.value() / 100.0,
                },
            },
            "whisper": {
                "model": self.whisper_model.currentText(),
                "language": self.whisper_lang.text(),
                "translate": self.whisper_translate.isChecked(),
                "threads": self.whisper_threads.value(),
                "on_demand_loading": self.on_demand.isChecked(),
                "gpu_isolation": self.gpu_isolation.isChecked(),
                "context_window_optimization": self.ctx_opt.isChecked(),
                **({"gpu_device": self.gpu_device.value()} if self.gpu_device.value() >= 0 else {}),
                "eager_processing": self.eager_processing.isChecked(),
                "eager_chunk_secs": self.eager_chunk_secs.value(),
                "eager_overlap_secs": self.eager_overlap_secs.value(),
            },
            "parakeet": {
                "model": self.parakeet_model.currentText(),
                "streaming": self.parakeet_streaming.isChecked(),
                "streaming_chunk_secs": self.streaming_chunk_secs.value(),
                "streaming_left_context_secs": self.streaming_left_context.value(),
                "streaming_right_context_secs": self.streaming_right_context.value(),
            },
            "output": {
                "mode": self.output_mode.currentText(),
                "fallback_to_clipboard": self.fallback_clip.isChecked(),
                "type_delay_ms": self.type_delay.value(),
                "pre_type_delay_ms": self.pre_type_delay.value(),
                "wait_for_modifier_release": self.wait_modifier_release.isChecked(),
                "modifier_release_timeout_ms": self.modifier_release_timeout.value(),
                "notification": {
                    "on_recording_start": self.notif_start.isChecked(),
                    "on_recording_stop": self.notif_stop.isChecked(),
                    "on_transcription": self.notif_transcription.isChecked(),
                },
            },
            "text": {
                "spoken_punctuation": self.spoken_punct.isChecked(),
                "smart_auto_submit": self.smart_auto_submit.isChecked(),
            },
            "status": {
                "icon_theme": self.icon_theme.currentText(),
            },
        }

        if replacements:
            config["text"]["replacements"] = replacements

        post_cmd = self.post_cmd.text().strip()
        if post_cmd:
            config["output"]["post_process"] = {
                "command": post_cmd,
                "timeout_ms": self.post_timeout.value(),
            }

        try:
            write_config(config)
            self.config = config
            if get_daemon_status():
                self._warn_health_issues()
                subprocess.run(
                    ["systemctl", "--user", "restart", "voxtype"],
                    timeout=10,
                )
                self.statusBar().showMessage("Config saved, daemon restarted")
            else:
                self.statusBar().showMessage("Config saved")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save config:\n{e}")

    def reload_config(self):
        self.config = read_config()
        self.icon_theme.setCurrentText(self._get("status", "icon_theme", default="emoji"))
        self.spoken_punct.setChecked(self._get("text", "spoken_punctuation", default=False))
        engine = self._get("engine", default="whisper")
        if engine in ENGINES:
            self.engine.setCurrentIndex(ENGINES.index(engine))
        self.audio_device.setText(self._get("audio", "device", default="default"))
        self.sample_rate.setValue(self._get("audio", "sample_rate", default=16000))
        self.max_duration.setValue(self._get("audio", "max_duration_secs", default=120))
        self.pause_media.setChecked(self._get("audio", "pause_media", default=False))
        self.whisper_model.setCurrentText(self._get("whisper", "model", default="small.en"))
        self.whisper_lang.setText(self._get("whisper", "language", default="en"))
        self.whisper_translate.setChecked(self._get("whisper", "translate", default=False))
        self.whisper_threads.setValue(self._get("whisper", "threads", default=8))
        self.on_demand.setChecked(self._get("whisper", "on_demand_loading", default=False))
        self.gpu_isolation.setChecked(self._get("whisper", "gpu_isolation", default=True))
        self.ctx_opt.setChecked(self._get("whisper", "context_window_optimization", default=True))
        gpu_dev = self._get("whisper", "gpu_device", default=-1)
        self.gpu_device.setValue(gpu_dev if gpu_dev is not None else -1)
        self.eager_processing.setChecked(self._get("whisper", "eager_processing", default=False))
        self.eager_chunk_secs.setValue(float(self._get("whisper", "eager_chunk_secs", default=5.0)))
        self.eager_overlap_secs.setValue(float(self._get("whisper", "eager_overlap_secs", default=0.5)))
        self.parakeet_model.setCurrentText(self._get("parakeet", "model", default="parakeet-tdt-0.6b-v3"))
        self.parakeet_streaming.setChecked(self._get("parakeet", "streaming", default=False))
        self.streaming_chunk_secs.setValue(float(self._get("parakeet", "streaming_chunk_secs", default=0.32)))
        self.streaming_left_context.setValue(float(self._get("parakeet", "streaming_left_context_secs", default=5.6)))
        self.streaming_right_context.setValue(float(self._get("parakeet", "streaming_right_context_secs", default=0.32)))
        self.smart_auto_submit.setChecked(self._get("text", "smart_auto_submit", default=False))
        self.output_mode.setCurrentText(self._get("output", "mode", default="type"))
        self.fallback_clip.setChecked(self._get("output", "fallback_to_clipboard", default=True))
        self.type_delay.setValue(self._get("output", "type_delay_ms", default=0))
        self.pre_type_delay.setValue(self._get("output", "pre_type_delay_ms", default=50))
        self.wait_modifier_release.setChecked(self._get("output", "wait_for_modifier_release", default=True))
        self.modifier_release_timeout.setValue(self._get("output", "modifier_release_timeout_ms", default=750))
        self.hotkey_enabled.setChecked(self._get("hotkey", "enabled", default=True))
        self.hotkey_key.setCurrentText(self._get("hotkey", "key", default="SCROLLLOCK"))
        self.hotkey_mode.setCurrentText(self._get("hotkey", "mode", default="toggle"))
        self.feedback_enabled.setChecked(self._get("audio", "feedback", "enabled", default=True))
        self.feedback_theme.setCurrentText(self._get("audio", "feedback", "theme", default="subtle"))
        vol = self._get("audio", "feedback", "volume", default=0.5)
        self.feedback_volume.setValue(int(vol * 100))
        self.notif_start.setChecked(self._get("output", "notification", "on_recording_start", default=True))
        self.notif_stop.setChecked(self._get("output", "notification", "on_recording_stop", default=False))
        self.notif_transcription.setChecked(self._get("output", "notification", "on_transcription", default=True))
        self._update_engine_visibility(self.engine.currentText())
        self._update_streaming_visibility(self.parakeet_streaming.isChecked())
        self.statusBar().showMessage("Config reloaded")

    def download_model(self):
        model = self.dl_model_combo.currentText()
        is_parakeet = model.startswith("parakeet-")

        # Whisper uses ggml-*.bin files; Parakeet uses directories.
        # get_installed_models() handles both, so just check that.
        if model in get_installed_models():
            QMessageBox.information(self, "Already Installed", f"Model '{model}' is already downloaded.")
            return

        # Parakeet requires the ONNX binary — give a useful error early if
        # the user picked one before switching backends.
        if is_parakeet and not backend_supports_onnx(get_current_backend()):
            reply = QMessageBox.question(
                self, "ONNX backend required",
                f"'{model}' is a Parakeet model and requires the ONNX backend.\n"
                "Click 'Enable ONNX (Parakeet, etc.)' first.\n\n"
                "Proceed with download anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.statusBar().showMessage(f"Downloading {model}... (this may take a moment)")
        QApplication.processEvents()

        proc = subprocess.run(
            ["voxtype", "setup", "--download", "--model", model, "--quiet"],
            capture_output=True, text=True, timeout=600,
        )

        if proc.returncode == 0:
            self.models_label.setText(f"Installed: {', '.join(get_installed_models())}")
            self.statusBar().showMessage(f"Model {model} downloaded successfully")
            QMessageBox.information(self, "Success", f"Model '{model}' downloaded!")
        else:
            err = proc.stderr or proc.stdout or "Unknown error"
            self.statusBar().showMessage("Download failed")
            QMessageBox.critical(self, "Error", f"Failed to download model:\n{err}")

    def closeEvent(self, event):
        # Hide to tray instead of quitting
        if self.tray:
            event.ignore()
            self.hide()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VoxType Settings")
    app.setDesktopFileName("voxtype-settings")
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # Fallback: no tray, just show settings window
        window = VoxTypeSettings()
        window.show()
        sys.exit(app.exec())

    tray = VoxTypeTray(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
