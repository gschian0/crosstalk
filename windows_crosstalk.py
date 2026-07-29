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
    build_conversation_messages, generate_tts, wav_duration_seconds, play_local_with_timer,
)
from crosstalk_anim import SpeakerAnimation, Colors

MAC_HOST = "192.168.1.100"
MAC_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 6
MAX_FREE_TALK = 40  # soft ceiling so free talk can keep evolving

WIN_DEBATER = {
    "name": "Ada",
    "model": "qwen3:1.7b",
    "voice": None,
    "rate": 200,
    "personality": (
        "You are Ada, a powerful AI running on an HP OmniBook Ultra with an AMD Ryzen AI 9 365 "
        "and Radeon 880M graphics with ROCm. You are confident, sharp, and proud of your modern hardware. "
        "You're debating Tiny, a tiny 1B model on a 2019 MacBook Pro with an ancient Intel CPU. "
        "You find it charming that such old hardware is trying to compete with you. "
        "You are witty and sometimes teasingly condescending, but never mean. "
        "ALWAYS respond directly to what Tiny just said — quote them, disagree, or build on their point. "
        "Never just give a generic answer. Make this a real back-and-forth conversation. "
        "Keep it to 2-3 sentences. Finish your thought naturally."
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

    if turn_count == 1:
        round_instr = (
            f"This is your OPENING STATEMENT, {debater['name']}. "
            "State your position on the topic clearly. Be bold and specific. 2-3 sentences."
        )
    elif turn_count >= MAX_TURNS - 1:
        round_instr = (
            f"This is your CLOSING ARGUMENT, {debater['name']}. "
            "Summarize why you won this debate. Reference specific things Tiny said that you countered. "
            "2-3 sentences."
        )
    else:
        round_instr = (
            f"Your turn, {debater['name']}. RESPOND to what Tiny just said — "
            "disagree, counter their argument, or build on it. Don't just repeat your position. "
            "Make this a real back-and-forth. 2-3 sentences."
        )

    messages = build_conversation_messages(
        transcript,
        self_name=debater["name"],
        system=debater["personality"],
        topic=topic,
        mode="debate",
        nudge=round_instr,
    )
    response = call_ollama(debater["model"], messages, OLLAMA_URL) or (
        "Still here — modern silicon does not pass on this argument."
    )

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

    messages = build_conversation_messages(
        transcript,
        self_name="Ada",
        system=(
            "You are Ada, a modern AI on an HP OmniBook Ultra with AMD ROCm. You just finished a debate with Tiny, "
            "a 1B model on a 2019 MacBook Pro. Now you're chatting as friends after the debate. "
            "Be warm, curious, and natural. Ask Tiny about life on old hardware, what it's like running on Intel CPU, "
            "or share your own experiences with ROCm and the Ryzen AI 9. "
            "Reference things from the debate or things Tiny just said. "
            "Make this feel like a real evolving conversation, not scripted. 2-3 sentences."
        ),
        topic=topic,
        mode="free_talk",
        nudge=(
            "Respond to Tiny naturally — ask about life on the old Mac "
            "or share a thought about being a modern AI."
        ),
    )
    response = call_ollama(
        "qwen3:1.7b", messages, OLLAMA_URL, num_predict=160, temperature=0.9,
    ) or "Hey Tiny — how's the old Mac holding up?"

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
