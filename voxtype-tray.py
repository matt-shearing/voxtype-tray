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
    QTabWidget, QLabel, QComboBox, QSpinBox, QCheckBox, QPushButton,
    QGroupBox, QFormLayout, QLineEdit, QSlider, QMessageBox,
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

HOTKEYS = [
    "SCROLLLOCK", "PAUSE", "RIGHTALT",
    "F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20",
    "F21", "F22", "F23", "F24",
]

ICON_THEMES = [
    "emoji", "nerd-font", "material", "phosphor", "codicons",
    "minimal", "dots", "arrows", "text",
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


def write_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
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

    for key in ["engine", "state_file"]:
        if key in config:
            lines.append(f'{key} = {write_value(config[key])}')
    lines.append("")

    sections = [
        ("hotkey", ["enabled", "key", "modifiers", "mode"]),
        ("audio", ["device", "sample_rate", "max_duration_secs"]),
        ("audio.feedback", ["enabled", "theme", "volume"]),
        ("whisper", [
            "mode", "model", "language", "translate", "threads",
            "on_demand_loading", "gpu_isolation", "context_window_optimization",
        ]),
        ("output", [
            "mode", "fallback_to_clipboard", "type_delay_ms",
            "pre_type_delay_ms", "paste_keys",
        ]),
        ("output.notification", [
            "on_recording_start", "on_recording_stop", "on_transcription",
        ]),
        ("output.post_process", ["command", "timeout_ms"]),
        ("text", ["spoken_punctuation", "replacements"]),
        ("status", ["icon_theme"]),
    ]

    for section_name, keys in sections:
        parts = section_name.split(".")
        data = config
        for part in parts:
            data = data.get(part, {})
            if not isinstance(data, dict):
                data = {}
                break
        if not data:
            continue
        lines.append(f"[{section_name}]")
        for key in keys:
            if key in data:
                lines.append(f"{key} = {write_value(data[key])}")
        lines.append("")

    with open(CONFIG_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def get_daemon_status():
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "voxtype"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


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
            subprocess.run(["systemctl", "--user", "start", "voxtype"], timeout=10)
        QTimer.singleShot(1500, self.refresh_state)

    def restart_daemon(self):
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
        tabs.addTab(self._build_whisper_tab(), "Whisper")
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

        group3 = QGroupBox("Models")
        vl = QVBoxLayout(group3)
        installed = get_installed_models()
        self.models_label = QLabel(
            f"Installed: {', '.join(installed)}" if installed else "No models installed"
        )
        vl.addWidget(self.models_label)

        dl_layout = QHBoxLayout()
        self.dl_model_combo = QComboBox()
        self.dl_model_combo.addItems(WHISPER_MODELS)
        dl_layout.addWidget(self.dl_model_combo)
        dl_btn = QPushButton("Download Model")
        dl_btn.clicked.connect(self.download_model)
        dl_layout.addWidget(dl_btn)
        vl.addLayout(dl_layout)
        layout.addWidget(group3)

        layout.addStretch()
        return w

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

    def _build_whisper_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        group = QGroupBox("Engine")
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

        group2 = QGroupBox("Performance")
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
        layout.addWidget(group2)

        layout.addStretch()
        return w

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
        layout.addWidget(group)

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
            subprocess.run(["systemctl", "--user", "start", "voxtype"], timeout=10)
            self.statusBar().showMessage("Daemon started")
        QTimer.singleShot(1000, self.refresh_status)

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
            "engine": "whisper",
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
            },
            "output": {
                "mode": self.output_mode.currentText(),
                "fallback_to_clipboard": self.fallback_clip.isChecked(),
                "type_delay_ms": self.type_delay.value(),
                "pre_type_delay_ms": self.pre_type_delay.value(),
                "notification": {
                    "on_recording_start": self.notif_start.isChecked(),
                    "on_recording_stop": self.notif_stop.isChecked(),
                    "on_transcription": self.notif_transcription.isChecked(),
                },
            },
            "text": {
                "spoken_punctuation": self.spoken_punct.isChecked(),
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
        self.audio_device.setText(self._get("audio", "device", default="default"))
        self.sample_rate.setValue(self._get("audio", "sample_rate", default=16000))
        self.max_duration.setValue(self._get("audio", "max_duration_secs", default=120))
        self.whisper_model.setCurrentText(self._get("whisper", "model", default="small.en"))
        self.whisper_lang.setText(self._get("whisper", "language", default="en"))
        self.whisper_translate.setChecked(self._get("whisper", "translate", default=False))
        self.whisper_threads.setValue(self._get("whisper", "threads", default=8))
        self.on_demand.setChecked(self._get("whisper", "on_demand_loading", default=False))
        self.gpu_isolation.setChecked(self._get("whisper", "gpu_isolation", default=True))
        self.ctx_opt.setChecked(self._get("whisper", "context_window_optimization", default=True))
        self.output_mode.setCurrentText(self._get("output", "mode", default="type"))
        self.fallback_clip.setChecked(self._get("output", "fallback_to_clipboard", default=True))
        self.type_delay.setValue(self._get("output", "type_delay_ms", default=0))
        self.pre_type_delay.setValue(self._get("output", "pre_type_delay_ms", default=50))
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
        self.statusBar().showMessage("Config reloaded")

    def download_model(self):
        model = self.dl_model_combo.currentText()
        dest = MODELS_DIR / f"ggml-{model}.bin"
        if dest.exists():
            QMessageBox.information(self, "Already Installed", f"Model '{model}' is already downloaded.")
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
