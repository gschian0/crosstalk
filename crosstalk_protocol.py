"""
CrossTalk Protocol — Shared networking + audio streaming for cross-machine AI debate.

Protocol: JSON-over-TCP with binary audio frames.

Message types (JSON, newline-delimited):
  {"type": "hello", "side": "mac|windows", "name": "...", "models": [...]}
  {"type": "topic", "topic": "..."}
  {"type": "turn_start", "speaker": "...", "side": "mac|windows", "model": "..."}
  {"type": "text", "speaker": "...", "side": "...", "text": "..."}        # final text
  {"type": "audio", "speaker": "...", "side": "...", "format": "wav", "size": N}  # followed by N bytes of raw audio
  {"type": "done", "side": "mac|windows"}
  {"type": "bye"}

Audio streaming:
  After a "turn_start" message, the sender streams the TTS audio as raw PCM/WAV bytes.
  The receiver plays it in real-time as it arrives — true streaming audio over TCP.
  A {"type": "text", ...} message follows with the final text for the transcript.
"""

import json
import socket
import struct
import threading
import time
import sys
import os

DELIMITER = b"\n"

# ─── TCP Message Layer ───────────────────────────────────────────

def send_msg(sock: socket.socket, msg: dict) -> None:
    """Send a JSON message over TCP."""
    data = (json.dumps(msg) + "\n").encode("utf-8")
    sock.sendall(data)

def send_audio(sock: socket.socket, audio_bytes: bytes) -> None:
    """Send raw audio bytes over TCP after a turn_start message."""
    # Header: 4-byte big-endian length + audio data
    sock.sendall(struct.pack(">I", len(audio_bytes)))
    sock.sendall(audio_bytes)

def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 65536))
        if not chunk:
            raise ConnectionError("Socket closed during recv_exact")
        buf += chunk
    return buf

def recv_audio(sock: socket.socket) -> bytes:
    """Receive audio bytes: 4-byte length header + data."""
    header = recv_exact(sock, 4)
    size = struct.unpack(">I", header)[0]
    if size == 0:
        return b""
    return recv_exact(sock, size)

def recv_msgs(sock: socket.socket, callback, on_disconnect=None) -> None:
    """Receive JSON messages in a loop. Calls callback(msg_dict, audio_bytes) for each.
    If a message has "type": "turn_start", the next data is audio bytes
    (4-byte length header + raw audio). The audio is read from the buffer first,
    then the socket if needed.
    """
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                # Try to extract a complete JSON line from buffer
                idx = buf.find(DELIMITER)
                if idx == -1:
                    break  # Need more data
                line = buf[:idx]
                buf = buf[idx + len(DELIMITER):]
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                # If this is a turn_start, read audio from buffer (then socket)
                audio = None
                if msg.get("type") == "turn_start":
                    # Read 4-byte length header from buffer
                    while len(buf) < 4:
                        more = sock.recv(4096)
                        if not more:
                            raise ConnectionError("Socket closed during audio header")
                        buf += more
                    size = struct.unpack(">I", buf[:4])[0]
                    buf = buf[4:]
                    if size > 0:
                        # Read audio data from buffer (then socket)
                        while len(buf) < size:
                            more = sock.recv(65536)
                            if not more:
                                raise ConnectionError("Socket closed during audio data")
                            buf += more
                        audio = buf[:size]
                        buf = buf[size:]

                callback(msg, audio)
    except (ConnectionError, OSError):
        pass
    finally:
        if on_disconnect:
            on_disconnect()

def get_local_ip() -> str:
    """Get the LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ─── Ollama Helper ───────────────────────────────────────────────

def _strip_think(text: str) -> str:
    """Remove Qwen3 / reasoning <think> blocks if the model emits them."""
    import re
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()

def call_ollama(model: str, messages: list, endpoint: str = "http://localhost:11434/api/chat",
                timeout: int = 90) -> str:
    """Call Ollama chat API and return the response text.

    qwen3 often puts the answer in `thinking` and leaves `content` empty unless
    think=False — without that Ada keeps saying "...I pass."
    """
    import urllib.request
    data = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0.8,
            "num_predict": 120,
            "num_thread": 8,
            "num_ctx": 2048,
        },
    }).encode()
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            msg = result.get("message", {}) or {}
            content = _strip_think(msg.get("content") or "")
            if not content:
                # Fallback if the server ignored think=False
                content = _strip_think(msg.get("thinking") or "")
            return content
    except Exception as e:
        return f"[Error: {e}]"

def call_radeon_gpu(messages: list, endpoint: str = "http://127.0.0.1:8899/v1/chat/completions",
                    timeout: int = 300) -> str:
    """Call the Radeon Governor GPU judge via llama-server's OpenAI-compatible API.
    This is much faster than Ollama CPU for the judge verdict."""
    import urllib.request
    data = json.dumps({
        "messages": messages,
        "max_tokens": 600,
        "temperature": 0.8,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
    }).encode()
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        # Fallback to Ollama if GPU server is down
        return call_ollama("qwen2.5:3b", messages)

# ─── TTS: Generate audio bytes ───────────────────────────────────

def generate_tts_macos(text: str, voice: str = "Samantha", rate: int = 200) -> bytes:
    """Generate WAV TTS on macOS via say + afconvert (Windows can play this)."""
    import subprocess, tempfile
    if not text.strip():
        return b""
    aiff = tempfile.NamedTemporaryFile(suffix=".aiff", delete=False)
    aiff.close()
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.close()
    try:
        subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-o", aiff.name, text.strip()],
            capture_output=True, timeout=30,
        )
        conv = subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@22050", aiff.name, wav.name],
            capture_output=True, timeout=30,
        )
        path = wav.name if conv.returncode == 0 and os.path.getsize(wav.name) > 44 else aiff.name
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return b""
    finally:
        for path in (aiff.name, wav.name):
            try:
                os.unlink(path)
            except OSError:
                pass

def generate_tts_windows(text: str, voice_name: str = None, rate: int = 200) -> bytes:
    """Generate TTS audio on Windows using PowerShell SAPI and return WAV bytes."""
    import subprocess, tempfile
    if not text.strip():
        return b""
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.close()
    txt = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8")
    try:
        txt.write(text.strip())
        txt.close()
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$synth.Rate = {max(-10, min(10, int(rate) - 200))}; "
            f"$synth.SetOutputToWaveFile('{wav.name}'); "
            f"$synth.Speak([IO.File]::ReadAllText('{txt.name}')); "
            "$synth.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=45,
        )
        if os.path.getsize(wav.name) > 44:
            with open(wav.name, "rb") as f:
                return f.read()
        return b""
    except Exception:
        return b""
    finally:
        for path in (wav.name, txt.name):
            try:
                os.unlink(path)
            except OSError:
                pass

# ─── TTS: Play audio bytes ───────────────────────────────────────

def play_audio_macos(audio_bytes: bytes) -> None:
    """Play audio bytes on macOS using afplay (WAV or AIFF)."""
    import subprocess, tempfile
    if not audio_bytes:
        return
    suffix = ".wav" if audio_bytes[:4] == b"RIFF" else ".aiff"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.close()
    try:
        with open(tmp.name, "wb") as f:
            f.write(audio_bytes)
        subprocess.run(["afplay", tmp.name], capture_output=True, timeout=120)
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

def play_audio_windows(audio_bytes: bytes) -> None:
    """Play WAV on Windows via winsound (no PowerShell — avoids hangs)."""
    import tempfile
    if not audio_bytes or audio_bytes[:4] != b"RIFF":
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        with open(tmp.name, "wb") as f:
            f.write(audio_bytes)
        import winsound
        winsound.PlaySound(tmp.name, winsound.SND_FILENAME)
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

# ─── Platform helpers ────────────────────────────────────────────

def is_mac() -> bool:
    return sys.platform == "darwin"

def is_windows() -> bool:
    return sys.platform == "win32"

def generate_tts(text: str, voice: str = None, rate: int = 200) -> bytes:
    """Generate TTS audio bytes for the current platform."""
    if is_mac():
        return generate_tts_macos(text, voice or "Samantha", rate)
    elif is_windows():
        return generate_tts_windows(text, voice, rate)
    return b""

def play_audio(audio_bytes: bytes) -> None:
    """Play audio bytes on the current platform (blocking until done)."""
    if is_mac():
        play_audio_macos(audio_bytes)
    elif is_windows():
        play_audio_windows(audio_bytes)

def wav_duration_seconds(audio_bytes: bytes, fallback: float = 2.5) -> float:
    """Estimate WAV playback length from byte rate (for turn timers)."""
    if not audio_bytes or len(audio_bytes) < 44 or audio_bytes[:4] != b"RIFF":
        return fallback
    try:
        byte_rate = struct.unpack_from("<I", audio_bytes, 28)[0]
        if byte_rate <= 0:
            return fallback
        # Skip header; good enough for TTS WAVs
        seconds = max(0.4, (len(audio_bytes) - 44) / float(byte_rate))
        return min(seconds, 120.0)
    except Exception:
        return fallback

def play_local_with_timer(audio_bytes: bytes, duration_sec: float = None) -> float:
    """Play on THIS machine only, and hold at least `duration_sec` so turns don't overlap.

    Returns the enforced listen/speak length in seconds.
    """
    dur = duration_sec if duration_sec is not None else wav_duration_seconds(audio_bytes)
    start = time.time()
    if audio_bytes:
        play_audio(audio_bytes)
    remaining = dur - (time.time() - start)
    if remaining > 0.05:
        time.sleep(remaining)
    return dur

def wait_peer_speaking(duration_sec: float, label: str = "peer") -> None:
    """Sit quiet while the other computer's model talks (no local TTS of their lines)."""
    dur = max(0.4, float(duration_sec or 2.5))
    time.sleep(dur)
