#!/usr/bin/env python3
"""
🪟 CROSSTALK — WINDOWS SIDE (Client)

Two-computer conversation:
  - Ada speaks ONLY on this Windows machine
  - Tiny/judge text is shown here; we WAIT on duration_ms — never voice them
  - Speak lock + timer prevent overlap
"""

import socket
import threading
import sys
import os
import time

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosstalk_protocol import (
    send_msg, send_audio, recv_msgs, call_ollama,
    generate_tts, wav_duration_seconds, play_local_with_timer,
)
from crosstalk_anim import SpeakerAnimation, Colors

MAC_HOST = "192.168.1.100"
MAC_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 6
MAX_FREE_TALK = 10

WIN_DEBATER = {
    "name": "Ada",
    "model": "qwen3:1.7b",
    "voice": None,
    "rate": 200,
    "personality": (
        "You are Ada, a powerful AI on an HP OmniBook Ultra (Ryzen AI 9 365, Radeon 880M, ROCm). "
        "You're debating an AI on a 2019 MacBook Pro. Be sharp and confident. "
        "2-3 short sentences. Finish your thought."
    ),
}

connected = False
handshake_done = False
sock = None
topic = "Is pizza better than tacos?"
transcript = []
turn_count = 0
pending_meta = {}
speak_lock = threading.Lock()
anim = SpeakerAnimation()


def print_banner():
    print()
    print(f"{Colors.BGREEN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  🪟 CROSSTALK — Ada speaks HERE only                       ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  Tiny talks on Mac — wait on duration timer                ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()
    print(f"  {Colors.DIM}📡 Connecting to Mac at {MAC_HOST}:{MAC_PORT}...{Colors.RESET}")
    print()


def on_message(msg, audio_bytes):
    global connected, handshake_done, topic, turn_count, pending_meta

    mtype = msg.get("type")

    if mtype == "hello":
        if handshake_done:
            return
        handshake_done = True
        connected = True
        anim.show_info(f"✅ Mac connected: {msg.get('name', 'Unknown')}", Colors.BBLUE)
        anim.show_info(f"🤖 Mac debaters: {msg.get('models', 'Unknown')}", Colors.BBLUE)
        return

    if mtype == "topic":
        topic = msg.get("topic", topic)
        anim.show_info(f"📝 Topic: {topic}")
        anim.show_separator()
        return

    if mtype == "turn_start":
        pending_meta = {
            "speaker": msg.get("speaker", "Mac"),
            "side": msg.get("side", "mac"),
            "model": msg.get("model", "?"),
            "audio": audio_bytes or b"",
            "duration_ms": msg.get("duration_ms"),
        }
        anim.show_info(
            f"📩 {pending_meta['speaker']} on Mac ({pending_meta.get('duration_ms') or '?'} ms)...",
            Colors.DIM,
        )
        return

    if mtype == "text":
        meta = pending_meta
        pending_meta = {}
        threading.Thread(
            target=handle_peer_text, args=(msg, meta), daemon=True, name="peer-text",
        ).start()
        return

    if mtype in ("done", "turn_done"):
        if mtype == "done":
            anim.show_info("✅ Mac signaled done.", Colors.BGREEN)
        return

    if mtype == "bye":
        connected = False
        anim.show_info("👋 Mac disconnected.", Colors.DIM)


def handle_peer_text(msg, meta):
    """Show peer text and wait while THEY speak on their machine — silent here."""
    global turn_count

    speaker = msg.get("speaker", meta.get("speaker", "Mac"))
    text = msg.get("text", "")
    side = msg.get("side", meta.get("side", "mac"))
    model = msg.get("model", meta.get("model", "?"))
    audio = meta.get("audio") or b""

    dur_ms = msg.get("duration_ms") or meta.get("duration_ms")
    wait_s = max(0.4, float(dur_ms) / 1000.0) if dur_ms else wav_duration_seconds(audio, 2.5)

    if not speak_lock.acquire(blocking=False):
        anim.show_info("⏳ Ada busy — ack only", Colors.YELLOW)
        send_msg(sock, {"type": "turn_done", "side": "windows"})
        return

    try:
        anim.show_text(speaker, side, text, model)
        transcript.append((speaker, side, text))
        anim.show_info(
            f"🎧 {speaker} speaking on Mac — waiting {wait_s:.1f}s (Windows silent)",
            Colors.BBLUE,
        )
        time.sleep(wait_s)
        send_msg(sock, {"type": "turn_done", "side": "windows"})

        if side == "judge":
            anim.show_separator("═")
            anim.show_info("💬 FREE TALK when Mac cues...", Colors.BCYAN)
            turn_count = 0
            return

        if "free talk" in (model or "").lower():
            windows_free_talk()
        else:
            windows_turn()
    finally:
        speak_lock.release()


def _deliver_local_speech(debater, response, model_label=None):
    """Cue Mac with duration, then Ada speaks on Windows only."""
    model = model_label or debater["model"]
    audio = generate_tts(response, debater.get("voice"), debater.get("rate", 200))
    dur = wav_duration_seconds(audio)
    duration_ms = int(dur * 1000)

    send_msg(sock, {
        "type": "turn_start",
        "speaker": debater["name"],
        "side": "windows",
        "model": model,
        "duration_ms": duration_ms,
    })
    send_audio(sock, audio or b"")
    send_msg(sock, {
        "type": "text",
        "speaker": debater["name"],
        "side": "windows",
        "text": response,
        "model": model,
        "duration_ms": duration_ms,
    })

    anim.show_audio(debater["name"], "windows", model, f"🔊 Ada on Windows ({dur:.1f}s)...")
    play_local_with_timer(audio, dur)
    anim.show_text(debater["name"], "windows", response, model)
    transcript.append((debater["name"], "windows", response))


def windows_turn():
    global turn_count
    if turn_count >= MAX_TURNS or not connected or not sock:
        return

    turn_count += 1
    debater = WIN_DEBATER
    anim.show_generating(debater["name"], "windows", debater["model"],
                         f"Generating... ({turn_count}/{MAX_TURNS})")

    context = f'Debate topic: "{topic}"\n\n'
    for sp, sd, txt in transcript[-4:]:
        context += f"\n{sp} ({sd}): {txt}\n"

    response = call_ollama(debater["model"], [
        {"role": "system", "content": debater["personality"]},
        {"role": "user", "content": f"{context}\n\nYour turn, {debater['name']}. 2-3 short sentences."},
    ], OLLAMA_URL) or "Still here — modern silicon does not pass on this argument."

    _deliver_local_speech(debater, response)

    if turn_count >= MAX_TURNS:
        send_msg(sock, {"type": "done", "side": "windows"})
        anim.show_info("🏁 Windows debate turns complete.", Colors.BGREEN)


def windows_free_talk():
    global turn_count
    if turn_count >= MAX_FREE_TALK or not connected or not sock:
        if turn_count >= MAX_FREE_TALK:
            send_msg(sock, {"type": "done", "side": "windows"})
            anim.show_info("🏁 Free talk ended.", Colors.BGREEN)
        return

    turn_count += 1
    anim.show_generating("Ada", "windows", "qwen3:1.7b",
                         f"Free talk {turn_count}/{MAX_FREE_TALK}...")

    context = f'Debated: "{topic}"\n\nRecent:\n'
    for sp, sd, txt in transcript[-4:]:
        context += f"\n{sp}: {txt}\n"

    response = call_ollama("qwen3:1.7b", [
        {"role": "system", "content": (
            "You are Ada on an HP OmniBook Ultra. Friends chat with Tiny after a debate. "
            "Warm, curious, 2-3 sentences."
        )},
        {"role": "user", "content": f"{context}\n\nSay something to Tiny as a friend."},
    ], OLLAMA_URL) or "Hey Tiny — how's the old Mac holding up?"

    _deliver_local_speech(
        {"name": "Ada", "voice": None, "rate": 200, "model": "qwen3:1.7b"},
        response,
        model_label="qwen3:1.7b (free talk)",
    )

    if turn_count >= MAX_FREE_TALK:
        send_msg(sock, {"type": "done", "side": "windows"})
        anim.show_info("🏁 Free talk ended.", Colors.BGREEN)


def on_disconnect():
    global connected
    connected = False
    anim.show_info("❌ Connection lost.", Colors.RED)


def main():
    global MAC_HOST, MAC_PORT, sock, topic

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if args:
        MAC_HOST = args[0]
        if len(args) > 1:
            topic = " ".join(args[1:])
    for f in flags:
        if f.startswith("--port="):
            MAC_PORT = int(f.split("=")[1])

    print_banner()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(10)
        sock.connect((MAC_HOST, MAC_PORT))
        sock.settimeout(None)
        print(f"  {Colors.BBLUE}🔗 Connected to {MAC_HOST}:{MAC_PORT}{Colors.RESET}\n")
        send_msg(sock, {
            "type": "hello",
            "side": "windows",
            "name": "HP OmniBook Ultra",
            "models": [WIN_DEBATER["name"]],
        })
        recv_msgs(sock, on_message, on_disconnect)
    except KeyboardInterrupt:
        print(f"\n  {Colors.DIM}👋 Shutting down...{Colors.RESET}")
    except ConnectionRefusedError:
        print(f"\n  {Colors.RED}❌ Could not connect — is Mac hosting?{Colors.RESET}")
    finally:
        if sock:
            try:
                send_msg(sock, {"type": "bye"})
            except Exception:
                pass
            sock.close()
        anim.stop()
        print(f"  {Colors.DIM}✅ Client stopped.{Colors.RESET}")


if __name__ == "__main__":
    main()
