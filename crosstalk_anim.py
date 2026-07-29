"""
CrossTalk Terminal Speaker Animation

Animated ASCII art that shows which side is speaking, with a "waveform"
animation while audio plays. Works on both Mac and Windows terminals.

Usage:
    anim = SpeakerAnimation()
    anim.show_speaking("Tiny", "mac", "tinyllama", "Generating response on Intel CPU...")
    # ... do work ...
    anim.show_audio("Tiny", "mac", "Streaming audio from Mac...")
    # ... play audio ...
    anim.stop()
"""

import threading
import time
import sys
import shutil

# ─── ANSI Colors ─────────────────────────────────────────────────

class Colors:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    # Bright variants
    BRED    = "\033[91m"
    BGREEN  = "\033[92m"
    BYELLOW = "\033[93m"
    BBLUE   = "\033[94m"
    BMAGENTA= "\033[95m"
    BCYAN   = "\033[96m"

# Side colors
SIDE_COLORS = {
    "mac":     Colors.BBLUE,    # Blue for Mac
    "windows": Colors.BGREEN,   # Green for Windows
    "judge":   Colors.BMAGENTA, # Magenta for Judge
}

SIDE_ICONS = {
    "mac":     "🍎",
    "windows": "🪟",
    "judge":   "⚖️",
}

# ─── Waveform characters (low to high) ───────────────────────────
WAVE_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

class SpeakerAnimation:
    """Animated terminal speaker display with waveform."""

    def __init__(self):
        self._thread = None
        self._stop = False
        self._speaker = ""
        self._side = ""
        self._model = ""
        self._status = ""
        self._mode = "idle"  # "generating", "audio", "idle"
        self._lock = threading.Lock()

    def _animate(self):
        """Animation loop — runs in a thread. Overwrites the same 2 lines."""
        frame = 0
        first_frame = True
        while not self._stop:
            with self._lock:
                speaker = self._speaker
                side = self._side
                model = self._model
                status = self._status
                mode = self._mode

            if mode == "idle":
                time.sleep(0.1)
                continue

            color = SIDE_COLORS.get(side, Colors.WHITE)
            icon = SIDE_ICONS.get(side, "🤖")

            # Build waveform animation
            if mode == "generating":
                dots = "." * ((frame % 3) + 1)
                line = f"  {icon} {color}{Colors.BOLD}{speaker}{Colors.RESET} {Colors.DIM}({model}){Colors.RESET} {Colors.YELLOW}⚡ thinking{dots}   {Colors.RESET}"
                wave = ""
            elif mode == "audio":
                import math
                wave_width = 30
                wave_bar = ""
                for i in range(wave_width):
                    val = int((math.sin((frame + i) * 0.3) * 0.5 + 0.5) * (len(WAVE_CHARS) - 1))
                    wave_bar += WAVE_CHARS[val]
                line = f"  {icon} {color}{Colors.BOLD}{speaker}{Colors.RESET} {Colors.DIM}({model}){Colors.RESET} {Colors.CYAN}🔊 speaking   {Colors.RESET}"
                wave = f"  {color}{wave_bar}{Colors.RESET}"
            else:
                line = f"  {icon} {color}{speaker}{Colors.RESET}"
                wave = ""

            # Move cursor up to overwrite previous frame (2 lines)
            if not first_frame:
                sys.stdout.write("\033[2A")  # Move up 2 lines
            sys.stdout.write("\033[2K\r")  # Clear line 1
            sys.stdout.write(f"{line}\n")
            sys.stdout.write("\033[2K\r")  # Clear line 2
            sys.stdout.write(f"{wave}\n")
            sys.stdout.flush()
            first_frame = False

            frame += 1
            time.sleep(0.12)

    def show_generating(self, speaker: str, side: str, model: str, status: str = ""):
        """Show 'generating/thinking' animation."""
        with self._lock:
            self._speaker = speaker
            self._side = side
            self._model = model
            self._status = status
            self._mode = "generating"
        self._start_thread()

    def show_audio(self, speaker: str, side: str, model: str, status: str = ""):
        """Show 'speaking/audio streaming' animation."""
        with self._lock:
            self._speaker = speaker
            self._side = side
            self._model = model
            self._status = status
            self._mode = "audio"
        self._start_thread()

    def show_text(self, speaker: str, side: str, text: str, model: str = ""):
        """Print the final text (stops animation first)."""
        self.stop()
        color = SIDE_COLORS.get(side, Colors.WHITE)
        icon = SIDE_ICONS.get(side, "🤖")
        model_tag = f" {Colors.DIM}({model}){Colors.RESET}" if model else ""
        # Wrap text at terminal width
        terminal_width = shutil.get_terminal_size((80, 24)).columns
        prefix = f"  {icon} {color}{Colors.BOLD}{speaker}{Colors.RESET}{model_tag}: "
        avail = terminal_width - len(prefix) - 1
        if avail < 20:
            avail = 60
        # Simple word wrap
        words = text.split()
        lines = []
        current = ""
        for w in words:
            if len(current) + len(w) + 1 <= avail:
                current = (current + " " + w).strip()
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)
        if not lines:
            lines = [""]

        sys.stdout.write(f"{prefix}{color}{lines[0]}{Colors.RESET}\n")
        for line in lines[1:]:
            sys.stdout.write(f"  {' ' * (len(icon) + 1)}{color}{line}{Colors.RESET}\n")
        sys.stdout.flush()

    def show_info(self, text: str, color: str = None):
        """Print an info line (no animation)."""
        self.stop()
        c = color or Colors.DIM
        sys.stdout.write(f"  {c}{text}{Colors.RESET}\n")
        sys.stdout.flush()

    def show_separator(self, char: str = "─", width: int = 60):
        """Print a separator line."""
        self.stop()
        sys.stdout.write(f"  {Colors.DIM}{char * width}{Colors.RESET}\n")
        sys.stdout.flush()

    def _start_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop = False
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the animation and clear the lines."""
        self._stop = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
        with self._lock:
            self._mode = "idle"
        # Clear the 2 animation lines
        sys.stdout.write("\033[2A\033[2K\r\033[2K\r")
        sys.stdout.flush()
