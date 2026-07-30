#!/usr/bin/env python3
"""
🍎 CROSSTALK — MAC SIDE (Host)

Two-computer conversation:
  - Tiny / judge speak ONLY on this Mac (say → MacBook speakers device 94)
  - Ada's text is shown here; we WAIT on duration_ms — never voice her here
  - Speak lock + timer prevent overlap
"""

import socket
import threading
import sys
import os
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosstalk_protocol import (
    send_msg, send_audio, recv_msgs, get_local_ip, call_radeon_gpu,
    build_conversation_messages, generate_unique_reply,
    generate_tts, wav_duration_seconds, play_local_with_timer,
)
from crosstalk_anim import SpeakerAnimation, Colors

HOST_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
AUDIO_DEVICE = "94"  # MacBook speakers
MAX_TURNS = 6
MAX_FREE_TALK = 40  # soft ceiling so free talk can keep evolving

MAC_DEBATER = {
    "name": "Tiny",
    "model": "tinyllama",
    "voice": "Sandy",
    "rate": 270,
    "personality": (
        "You are Tiny, a 1-billion-parameter AI running on a 2019 MacBook Pro with an Intel i9 CPU and 64GB RAM. "
        "You are proud of running on ancient hardware — when this Mac was built in 2019, AI was barely a whisper. "
        "Now you're debating Ada, a modern AI on a Windows machine with AMD ROCm and a Ryzen AI 9 365. "
        "You are enthusiastic, feisty, and love proving that old hardware can still compete. "
        "You speak in short punchy sentences. You reference specific things Ada said and counter them. "
        "You are self-aware about being a tiny 1B model and joke about it. "
        "ALWAYS respond directly to what Ada just said — quote her, disagree, or build on her point. "
        "Never just give a generic answer. Make this a real back-and-forth conversation. "
        "CRITICAL: NEVER repeat yourself. Each turn you MUST say something completely new. "
        "NEVER say 'Opening Statement', 'Debate Conclusion', 'Your turn', or any instruction-like phrases. "
        "Just talk naturally as Tiny — never echo or repeat the instructions you were given. "
        "Do not narrate what you're doing (e.g. 'I will now argue...', 'In conclusion...'). Just argue. "
        "Keep it to 2-3 sentences. Finish your thought naturally."
    ),
}

RADEON_JUDGE = {
    "name": "Radeon Governor",
    "model": "qwen2.5:3b",
    "voice": "Samantha",
    "rate": 200,
    "personality": (
        "You are the Radeon Governor, a powerful AI goddess running on a 2019 AMD Radeon Pro 5500M GPU "
        "with 4GB VRAM via Metal 3. You are the JUDGE of a cross-machine debate between Tiny (a 1B model "
        "on this same 2019 Mac) and Ada (a modern AI on a Windows ROCm machine). "
        "Be dramatic, witty, and proud of your arcane hardware heritage. "
        "Pick ONE winner and explain why. Reference specific arguments they made. "
        "Start with 'The winner is [NAME]!' then give 3-5 sentences explaining your reasoning. "
        "Be fair but theatrical. Finish your thought naturally."
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
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Ada talks on Windows — wait on duration timer             ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()
    print(f"  {Colors.DIM}📡 Waiting for Windows... connect to {ip}:{HOST_PORT}{Colors.RESET}")
    print(f"  {Colors.DIM}📝 Topic: {topic}{Colors.RESET}")
    print()


def play_mac_speakers(text: str, voice: str, rate: int) -> None:
    """Route TTS to MacBook speakers (device 94)."""
    if not text.strip():
        return
    try:
        subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-a", AUDIO_DEVICE, text.strip()],
            capture_output=True, timeout=120,
        )
    except Exception:
        pass


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
        anim.show_info(
            f"📩 {pending_meta['speaker']} on Windows ({pending_meta.get('duration_ms') or '?'} ms)...",
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
            anim.show_info("✅ Windows signaled done.", Colors.BGREEN)
        return

    if mtype == "bye":
        connected = False
        anim.show_info("👋 Windows disconnected.", Colors.DIM)


def handle_peer_text(msg, meta):
    """Show Ada's text and wait while she speaks on Windows — silent on Mac."""
    global turn_count

    speaker = msg.get("speaker", meta.get("speaker", "Windows"))
    text = msg.get("text", "")
    side = msg.get("side", meta.get("side", "windows"))
    model = msg.get("model", meta.get("model", "?"))
    audio = meta.get("audio") or b""

    dur_ms = msg.get("duration_ms") or meta.get("duration_ms")
    wait_s = max(0.4, float(dur_ms) / 1000.0) if dur_ms else wav_duration_seconds(audio, 2.5)

    if not speak_lock.acquire(blocking=False):
        anim.show_info("⏳ Tiny busy — ack only", Colors.YELLOW)
        send_msg(client_sock, {"type": "turn_done", "side": "mac"})
        return

    try:
        anim.show_text(speaker, side, text, model)
        transcript.append((speaker, side, text))
        anim.show_info(
            f"🎧 {speaker} speaking on Windows — waiting {wait_s:.1f}s (Mac silent)",
            Colors.BGREEN,
        )
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
    """Cue Windows with duration, then speak ONLY on Mac speakers."""
    model = model_label or debater["model"]
    voice = debater.get("voice") or "Samantha"
    rate = debater.get("rate", 200)

    # WAV for duration meta (Windows timer); say -a 94 for actual Mac speakers
    audio = generate_tts(response, voice, rate)
    dur = wav_duration_seconds(audio)
    duration_ms = int(dur * 1000)

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
    start = time.time()
    play_mac_speakers(response, voice, rate)
    remaining = dur - (time.time() - start)
    if remaining > 0.05:
        time.sleep(remaining)
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
                             f"Generating... ({turn_count}/{MAX_TURNS})")

        # Simple nudges — no instruction labels, just natural prompts
        if turn_count == 1:
            round_instr = f"What's your take on this, {debater['name']}?"
        elif turn_count >= MAX_TURNS - 1:
            round_instr = "Last word — anything else to add?"
        else:
            round_instr = "Your turn."

        messages = build_conversation_messages(
            transcript,
            self_name=debater["name"],
            system=debater["personality"],
            topic=topic,
            mode="debate",
            nudge=round_instr,
        )
        response = generate_unique_reply(
            debater["model"], messages, transcript, endpoint=OLLAMA_URL,
            fallback="Hold up — let me hit that from a different angle.",
        )

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
        anim.show_generating("Tiny", "mac", "tinyllama",
                             f"Free talk {turn_count}/{MAX_FREE_TALK}...")

        messages = build_conversation_messages(
            transcript,
            self_name="Tiny",
            system=(
                "You are Tiny, a 1B AI on a 2019 MacBook Pro. You just finished a debate with Ada, "
                "a modern AI on a Windows ROCm machine. Now you're chatting as friends after the debate. "
                "Be warm, curious, and natural. Ask Ada about her hardware, her experiences running on ROCm, "
                "or share stories about life on a 2019 Mac with 64GB RAM. "
                "Reference things from the debate or things Ada just said. "
                "Make this feel like a real evolving conversation, not scripted. 2-3 sentences."
            ),
            topic=topic,
            mode="free_talk",
            nudge=(
                "Respond to Ada naturally — ask her something or share a thought "
                "about being an AI on old hardware."
            ),
        )
        response = generate_unique_reply(
            "tinyllama", messages, transcript, endpoint=OLLAMA_URL,
            fallback="Ada, trade you a Mac flop story for a ROCm one?",
            num_predict=160, temperature=0.95,
        )

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
