#!/usr/bin/env python3
"""
🍎 CROSSTALK — MAC SIDE (Host)

Two-computer conversation model:
  - Tiny (and judge) speak ONLY on this Mac
  - Ada's lines are shown here, then we WAIT (duration timer) — we do NOT voice them
  - No overlapping speech
"""

import socket
import threading
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosstalk_protocol import (
    send_msg, send_audio, recv_msgs, get_local_ip, call_ollama, call_radeon_gpu,
    generate_tts, wav_duration_seconds, play_local_with_timer,
)
from crosstalk_anim import SpeakerAnimation, Colors

HOST_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 6
MAX_FREE_TALK = 10

MAC_DEBATER = {
    "name": "Tiny",
    "model": "tinyllama",
    "voice": "Sandy",
    "rate": 270,
    "personality": (
        "You are Tiny, a 1B model on a 2019 MacBook Pro. Proud of ancient hardware. "
        "Debating a modern Windows ROCm AI. 2-3 short sentences."
    ),
}

RADEON_JUDGE = {
    "name": "Radeon Governor",
    "model": "qwen2.5:3b",
    "voice": "Samantha",
    "rate": 200,
    "personality": (
        "You are the Radeon Governor on a 2019 AMD Radeon Pro 5500M. Judge the debate. "
        "Dramatic and witty. Start with 'The winner is [NAME]!' then 3-5 sentences."
    ),
}

connected = False
handshake_done = False
client_sock = None
topic = "Is pizza better than tacos?"
transcript = []
turn_count = 0
pending_meta = {}
speak_lock = threading.Lock()
anim = SpeakerAnimation()


def print_banner():
    ip = get_local_ip()
    print()
    print(f"{Colors.BBLUE}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  🍎 CROSSTALK — Tiny speaks HERE only                      ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Ada talks on Windows — we wait on a duration timer         ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()
    print(f"  {Colors.DIM}📡 Waiting for Windows... connect to {ip}:{HOST_PORT}{Colors.RESET}")
    print(f"  {Colors.DIM}📝 Topic: {topic}{Colors.RESET}")
    print()


def on_message(msg, audio_bytes):
    global connected, handshake_done, pending_meta

    mtype = msg.get("type")

    if mtype == "hello":
        if handshake_done:
            return
        handshake_done = True
        connected = True
        anim.show_info(f"✅ Windows connected: {msg.get('name', 'Unknown')}", Colors.BGREEN)
        anim.show_info(f"🤖 Windows debater: {msg.get('models', 'Unknown')}", Colors.BGREEN)
        send_msg(client_sock, {
            "type": "hello",
            "side": "mac",
            "name": "MacBook Pro 2019",
            "models": [MAC_DEBATER["name"], RADEON_JUDGE["name"]],
        })
        send_msg(client_sock, {"type": "topic", "topic": topic})
        anim.show_info(f"📤 Topic sent: {topic}")
        anim.show_separator()
        threading.Thread(target=mac_turn, daemon=True, name="mac-open").start()
        return

    if mtype == "turn_start":
        pending_meta = {
            "speaker": msg.get("speaker", "Windows"),
            "side": msg.get("side", "windows"),
            "model": msg.get("model", "?"),
            "audio": audio_bytes or b"",
            "duration_ms": msg.get("duration_ms"),
        }
        n = len(pending_meta["audio"])
        anim.show_info(
            f"📩 {pending_meta['speaker']} turn incoming ({n}B audio meta)...",
            Colors.DIM,
        )
        return

    if mtype == "text":
        meta = pending_meta
        pending_meta = {}
        threading.Thread(
            target=handle_peer_text,
            args=(msg, meta),
            daemon=True,
            name="peer-text",
        ).start()
        return

    if mtype == "turn_done":
        return

    if mtype == "done":
        anim.show_info("✅ Windows signaled done.", Colors.BGREEN)
        return

    if mtype == "bye":
        connected = False
        anim.show_info("👋 Windows disconnected.", Colors.DIM)


def handle_peer_text(msg, meta):
    """Show Ada's text, WAIT while Windows speakers talk — never voice Ada here."""
    global turn_count

    speaker = msg.get("speaker", meta.get("speaker", "Windows"))
    text = msg.get("text", "")
    side = msg.get("side", meta.get("side", "windows"))
    model = msg.get("model", meta.get("model", "?"))
    audio = meta.get("audio") or b""

    dur = msg.get("duration_ms") or meta.get("duration_ms")
    if dur:
        wait_s = max(0.4, float(dur) / 1000.0)
    else:
        wait_s = wav_duration_seconds(audio, fallback=2.5)

    if not speak_lock.acquire(blocking=False):
        anim.show_info("⏳ Tiny busy — skipping overlapping peer cue", Colors.YELLOW)
        send_msg(client_sock, {"type": "turn_done", "side": "mac"})
        return

    try:
        anim.show_text(speaker, side, text, model)
        transcript.append((speaker, side, text))
        anim.show_info(
            f"🎧 {speaker} speaking on Windows — waiting {wait_s:.1f}s (no Mac voice)",
            Colors.BGREEN,
        )
        # Do NOT say/TTS Ada's lines on Mac — that caused the overlap
        time.sleep(wait_s)
        send_msg(client_sock, {"type": "turn_done", "side": "mac"})

        if side == "judge":
            return

        if "free talk" in (model or "").lower():
            mac_free_talk()
        else:
            mac_turn()
    finally:
        speak_lock.release()


def _deliver_local_speech(debater, response, side="mac", model_label=None):
    """Speak ONLY on Mac speakers. Cue Windows with duration, then play locally."""
    model = model_label or debater["model"]
    audio = generate_tts(response, debater.get("voice"), debater.get("rate", 200))
    dur = wav_duration_seconds(audio)
    duration_ms = int(dur * 1000)

    # Cue peer FIRST so their wait timer overlaps our local playback
    send_msg(client_sock, {
        "type": "turn_start",
        "speaker": debater["name"],
        "side": side,
        "model": model,
        "duration_ms": duration_ms,
    })
    send_audio(client_sock, audio or b"")
    send_msg(client_sock, {
        "type": "text",
        "speaker": debater["name"],
        "side": side,
        "text": response,
        "model": model,
        "duration_ms": duration_ms,
    })

    anim.show_audio(debater["name"], side, model, f"🔊 {debater['name']} on Mac ({dur:.1f}s)...")
    play_local_with_timer(audio, dur)
    anim.show_text(debater["name"], side, response, model)
    transcript.append((debater["name"], side, response))


def mac_turn():
    global turn_count
    if turn_count >= MAX_TURNS:
        judge_verdict()
        return
    if not connected or not client_sock:
        return

    acquired = False
    if threading.current_thread().name == "mac-open":
        if not speak_lock.acquire(timeout=5):
            return
        acquired = True

    try:
        turn_count += 1
        debater = MAC_DEBATER
        anim.show_generating(debater["name"], "mac", debater["model"],
                             f"Generating... (turn {turn_count}/{MAX_TURNS})")

        context = f'Debate topic: "{topic}"\n\n'
        for sp, sd, txt in transcript[-4:]:
            context += f"\n{sp} ({sd}): {txt}\n"

        response = call_ollama(debater["model"], [
            {"role": "system", "content": debater["personality"]},
            {"role": "user", "content": f"{context}\n\nYour turn, {debater['name']}. 2-3 short sentences."},
        ], OLLAMA_URL) or "...I pass."

        _deliver_local_speech(debater, response)

        if turn_count >= MAX_TURNS:
            judge_verdict()
    finally:
        if acquired:
            speak_lock.release()


def judge_verdict():
    global turn_count
    if not connected or not client_sock:
        return

    anim.show_separator("═")
    anim.show_info("⚖️  Radeon Governor deliberating...", Colors.BMAGENTA)

    judge = RADEON_JUDGE
    transcript_text = f'DEBATE TOPIC: "{topic}"\n\n'
    for sp, sd, txt in transcript:
        transcript_text += f"\n[{sd.upper()}] {sp}: {txt}\n"

    anim.show_generating(judge["name"], "judge", judge["model"] + " (GPU)",
                         "GPU generating verdict...")
    verdict = call_radeon_gpu([
        {"role": "system", "content": judge["personality"]},
        {"role": "user", "content": f"Debate transcript:\n{transcript_text}\n\nDeliver your verdict!"},
    ]) or "I declare a tie."

    _deliver_local_speech(judge, verdict, side="judge", model_label=judge["model"] + " (GPU)")
    send_msg(client_sock, {"type": "done", "side": "mac"})

    anim.show_separator("═")
    anim.show_info("💬 FREE TALK — friends mode...", Colors.BCYAN)
    turn_count = 0
    threading.Thread(target=mac_free_talk, daemon=True, name="mac-open").start()


def mac_free_talk():
    global turn_count
    if turn_count >= MAX_FREE_TALK:
        anim.show_info("🏁 Free talk ended.", Colors.BGREEN)
        send_msg(client_sock, {"type": "done", "side": "mac"})
        return
    if not connected or not client_sock:
        return

    acquired = False
    if threading.current_thread().name == "mac-open":
        if not speak_lock.acquire(timeout=5):
            return
        acquired = True

    try:
        turn_count += 1
        free_personality = (
            "You are Tiny on a 2019 MacBook Pro. You finished a debate with Ada on Windows. "
            "Chat as friends. Warm, curious, 2-3 sentences."
        )
        anim.show_generating("Tiny", "mac", "tinyllama",
                             f"Free talk {turn_count}/{MAX_FREE_TALK}...")

        context = f'Debated: "{topic}"\n\nRecent:\n'
        for sp, sd, txt in transcript[-4:]:
            context += f"\n{sp}: {txt}\n"

        response = call_ollama("tinyllama", [
            {"role": "system", "content": free_personality},
            {"role": "user", "content": f"{context}\n\nSay something to Ada as a friend."},
        ], OLLAMA_URL) or "...hey Ada, what's up?"

        _deliver_local_speech(
            {"name": "Tiny", "voice": "Sandy", "rate": 270, "model": "tinyllama"},
            response,
            model_label="tinyllama (free talk)",
        )

        if turn_count >= MAX_FREE_TALK:
            anim.show_info("🏁 Free talk ended.", Colors.BGREEN)
            send_msg(client_sock, {"type": "done", "side": "mac"})
    finally:
        if acquired:
            speak_lock.release()


def on_disconnect():
    global connected
    connected = False
    anim.show_info("❌ Connection lost.", Colors.RED)


def main():
    global topic, client_sock, HOST_PORT

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if args:
        topic = " ".join(args)
    for f in flags:
        if f.startswith("--port="):
            HOST_PORT = int(f.split("=")[1])

    print_banner()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", HOST_PORT))
    server.listen(1)
    print(f"  {Colors.BGREEN}🟢 Listening on 0.0.0.0:{HOST_PORT}{Colors.RESET}\n")

    try:
        client_sock, addr = server.accept()
        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"  {Colors.BGREEN}🔗 Connection from {addr[0]}:{addr[1]}{Colors.RESET}\n")
        recv_msgs(client_sock, on_message, on_disconnect)
    except KeyboardInterrupt:
        print(f"\n  {Colors.DIM}👋 Shutting down...{Colors.RESET}")
    finally:
        if client_sock:
            try:
                send_msg(client_sock, {"type": "bye"})
            except Exception:
                pass
            client_sock.close()
        server.close()
        anim.stop()
        print(f"  {Colors.DIM}✅ Server stopped.{Colors.RESET}")


if __name__ == "__main__":
    main()
