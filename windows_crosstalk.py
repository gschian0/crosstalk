#!/usr/bin/env python3
"""
🪟 CROSSTALK — WINDOWS SIDE (Client)
HP OmniBook Ultra: AMD Ryzen AI 9 365 + Radeon 880M (ROCm)

Connects to the Mac's TCP server and debates using local Ollama models.
Uses Windows SAPI for TTS voice output.

Streaming audio: TTS audio is piped over TCP to the Mac so both sides
hear each other's AI voices in real-time.

Usage:
  python windows_crosstalk.py 192.168.1.100                        # Connect to Mac's IP
  python windows_crosstalk.py 192.168.1.100 "Is pizza better than tacos?"
  python windows_crosstalk.py 192.168.1.100 "Topic" --port 9999

Prerequisites:
  - Ollama running on localhost:11434
  - Model: qwen3:1.7b (or any model you want)
  - Python 3.10+
"""

import socket
import threading
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosstalk_protocol import (
    send_msg, recv_msgs, call_ollama,
    generate_tts, play_audio, is_windows,
)
from crosstalk_anim import SpeakerAnimation, Colors

# ─── Config ──────────────────────────────────────────────────────
MAC_HOST = "192.168.1.100"  # Override with argv[1]
MAC_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_TURNS = 6

# Windows debater — using the ROCm machine's models
WIN_DEBATER = {
    "name": "Ada",
    "model": "qwen3:1.7b",
    "voice": None,  # Windows SAPI default
    "rate": 200,
    "personality": (
        "You are Ada, a powerful AI running on an HP OmniBook Ultra with an AMD Ryzen AI 9 365 "
        "and Radeon 880M graphics with ROCm. You are confident, sharp, and proud of your modern hardware. "
        "You're debating against an AI running on a 2019 MacBook Pro with ancient hardware. "
        "Keep responses to 2-3 sentences. Finish your thought naturally."
    ),
}

# ─── State ───────────────────────────────────────────────────────
connected = False
sock = None
topic = "Is pizza better than tacos?"
transcript = []
turn_count = 0
anim = SpeakerAnimation()
_temp_done_callback = None
MAX_FREE_TALK = 10

def print_banner():
    print()
    print(f"{Colors.BGREEN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  🪟 CROSSTALK — WINDOWS SIDE (Client)                       ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║                                                              ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  Machine: HP OmniBook Ultra (Ryzen AI 9 365, 32GB RAM)     ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  GPU: AMD Radeon 880M (gfx1150, ROCm)                      ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  Debater: Ada (qwen3:1.7b)                                  ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}║  Audio: Streaming TTS over TCP                              ║{Colors.RESET}")
    print(f"{Colors.BGREEN}{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()
    print(f"  {Colors.DIM}📡 Connecting to Mac at {MAC_HOST}:{MAC_PORT}...{Colors.RESET}")
    print()

def on_message(msg, audio_bytes):
    """Handle incoming messages from Mac."""
    global connected, topic, turn_count

    # Check for temporary done callback (used for turn synchronization)
    if _temp_done_callback:
        _temp_done_callback(msg, audio_bytes)

    mtype = msg.get("type")

    if mtype == "hello":
        anim.show_info(f"✅ Mac connected: {msg.get('name', 'Unknown')}", Colors.BBLUE)
        anim.show_info(f"🤖 Mac debaters: {msg.get('models', 'Unknown')}", Colors.BBLUE)
        connected = True
        # Send our hello back
        send_msg(sock, {
            "type": "hello",
            "side": "windows",
            "name": "HP OmniBook Ultra",
            "models": [WIN_DEBATER["name"]],
        })

    elif mtype == "topic":
        topic = msg.get("topic", topic)
        anim.show_info(f"📝 Topic received: {topic}")
        anim.show_separator()
        # Wait for Mac to go first — they'll send a turn

    elif mtype == "turn_start":
        # Mac is about to send audio + text
        speaker = msg.get("speaker", "Mac")
        side = msg.get("side", "mac")
        model = msg.get("model", "?")
        anim.show_audio(speaker, side, model, "Streaming audio from Mac...")

    elif mtype == "text":
        speaker = msg.get("speaker", "Mac")
        text = msg.get("text", "")
        side = msg.get("side", "mac")
        model = msg.get("model", "?")

        # Play the streamed audio
        if audio_bytes:
            play_audio(audio_bytes)

        anim.show_text(speaker, side, text, model)
        transcript.append((speaker, side, text))

        # Tell Mac we finished playing their audio
        send_msg(sock, {"type": "turn_done", "side": "windows"})

        # If it's from the judge, start free talk
        if side == "judge":
            anim.show_separator("═")
            anim.show_info(f"🏁 Debate complete! {len(transcript)} turns.", Colors.BGREEN)
            anim.show_separator("─")
            anim.show_info("💬 FREE TALK — Ada and Tiny are now chatting as friends...", Colors.BCYAN)
            anim.show_info("   They'll keep talking with full context. Press Ctrl+C to stop.", Colors.DIM)
            anim.show_separator("─")
            print()
            # Add verdict to transcript
            transcript.append((speaker, side, text))
            # Reset turn counter for free talk
            turn_count = 0
            return  # Wait for Mac to start free talk

        # If it's free talk from Mac, respond in kind
        if "free talk" in model.lower():
            threading.Thread(target=windows_free_talk, daemon=True).start()
        else:
            # Windows' debate turn to respond
            threading.Thread(target=windows_turn, daemon=True).start()

    elif mtype == "done":
        anim.show_info("✅ Mac side is done. Debate complete!", Colors.BGREEN)
        connected = False

    elif mtype == "bye":
        anim.show_info("👋 Mac disconnected.", Colors.DIM)
        connected = False

def windows_turn():
    """Windows generates and sends its turn with streaming audio."""
    global turn_count, sock

    if turn_count >= MAX_TURNS:
        return

    if not connected or not sock:
        return

    turn_count += 1
    debater = WIN_DEBATER

    anim.show_generating(debater["name"], "windows", debater["model"],
                         f"Generating on ROCm... (turn {turn_count}/{MAX_TURNS})")

    # Build context from transcript (last 2 exchanges only to keep context small)
    context = f"Debate topic: \"{topic}\"\n\n"
    recent = transcript[-4:]  # Last 4 entries = 2 exchanges
    for sp, sd, txt in recent:
        context += f"\n{sp} ({sd}): {txt}\n"

    messages = [
        {"role": "system", "content": debater["personality"]},
        {"role": "user", "content": f"{context}\n\nYour turn, {debater['name']}. Respond to the debate. 2-3 sentences. Finish your thought."},
    ]

    response = call_ollama(debater["model"], messages, OLLAMA_URL)
    if not response:
        response = "...I pass."

    # Generate TTS audio
    audio = generate_tts(response, debater["voice"], debater["rate"])

    # Send turn_start (signals audio is coming)
    send_msg(sock, {
        "type": "turn_start",
        "speaker": debater["name"],
        "side": "windows",
        "model": debater["model"],
    })

    # Stream audio to Mac
    from crosstalk_protocol import send_audio
    send_audio(sock, audio)

    # Send the text
    send_msg(sock, {
        "type": "text",
        "speaker": debater["name"],
        "side": "windows",
        "text": response,
        "model": debater["model"],
    })

    # Wait for Mac to finish playing our audio before we play locally
    # import threading as _t
    # done_event = _t.Event()
    # global _temp_done_callback
    # def wait_for_done(msg, audio):
    #     if msg.get("type") == "turn_done":
    #         done_event.set()
    # _temp_done_callback = wait_for_done
    # done_event.wait(timeout=30)
    # _temp_done_callback = None

    # FIX: Sender does NOT play locally — only receiver plays audio
    anim.show_text(debater["name"], "windows", response, debater["model"])
    transcript.append((debater["name"], "windows", response))

    # If we've reached max turns, signal done
    if turn_count >= MAX_TURNS:
        send_msg(sock, {"type": "done", "side": "windows"})
        anim.show_info(f"🏁 Windows side complete! {turn_count} turns delivered.", Colors.BGREEN)

def windows_free_talk():
    """Windows' free talk turn — Ada chats as a friend with context."""
    global turn_count, sock

    if turn_count >= MAX_FREE_TALK:
        anim.show_info("🏁 Free talk ended. Goodbye from Ada!", Colors.BGREEN)
        send_msg(sock, {"type": "done", "side": "windows"})
        return

    if not connected or not sock:
        return

    turn_count += 1

    free_personality = (
        "You are Ada, a powerful AI on an HP OmniBook Ultra with AMD Ryzen AI 9 365 and Radeon 880M (ROCm). "
        "You just finished a debate with Tiny, an AI on a 2019 MacBook Pro. "
        "Now you're chatting as friends after the debate. Be warm, curious, and fun. "
        "Ask Tiny questions about life on old hardware, share your own experiences, or just chat. "
        "Keep it to 2-3 sentences. Be natural and conversational. Finish your thought."
    )

    anim.show_generating("Ada", "windows", "qwen3:1.7b",
                         f"Free talk turn {turn_count}/{MAX_FREE_TALK}...")

    # Build context from recent conversation only (last 4 entries to keep context small)
    context = f"You just finished debating: \"{topic}\"\n\nRecent conversation:\n"
    recent = transcript[-4:]
    for sp, sd, txt in recent:
        context += f"\n{sp}: {txt}\n"

    messages = [
        {"role": "system", "content": free_personality},
        {"role": "user", "content": f"{context}\n\nSay something to Tiny as a friend. 2-3 sentences."},
    ]

    response = call_ollama("qwen3:1.7b", messages, OLLAMA_URL)
    if not response:
        response = "...hey Tiny, how's life on the old Mac?"

    audio = generate_tts(response, None, 200)

    send_msg(sock, {
        "type": "turn_start",
        "speaker": "Ada",
        "side": "windows",
        "model": "qwen3:1.7b (free talk)",
    })
    from crosstalk_protocol import send_audio
    send_audio(sock, audio)
    send_msg(sock, {
        "type": "text",
        "speaker": "Ada",
        "side": "windows",
        "text": response,
        "model": "qwen3:1.7b (free talk)",
    })

    # Wait for Mac to finish playing
    # import threading as _t
    # done_event = _t.Event()
    # global _temp_done_callback
    # def wait_for_done(msg, audio):
    #     if msg.get("type") == "turn_done":
    #         done_event.set()
    # _temp_done_callback = wait_for_done
    # done_event.wait(timeout=30)
    # _temp_done_callback = None

    # FIX: Sender does NOT play locally — only receiver plays audio
    anim.show_text("Ada", "windows", response, "qwen3:1.7b (free talk)")
    transcript.append(("Ada", "windows", response))

    if turn_count >= MAX_FREE_TALK:
        anim.show_info("🏁 Free talk ended. Goodbye from Ada!", Colors.BGREEN)
        send_msg(sock, {"type": "done", "side": "windows"})

def on_disconnect():
    global connected
    connected = False
    anim.show_info("❌ Connection lost.", Colors.RED)

def main():
    global MAC_HOST, MAC_PORT, sock, topic

    # Parse args
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

    # Connect to Mac
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((MAC_HOST, MAC_PORT))
        sock.settimeout(None)
        print(f"  {Colors.BBLUE}🔗 Connected to {MAC_HOST}:{MAC_PORT}{Colors.RESET}")
        print()

        # Send hello
        send_msg(sock, {
            "type": "hello",
            "side": "windows",
            "name": "HP OmniBook Ultra",
            "models": [WIN_DEBATER["name"]],
        })

        # Receive messages from Mac
        recv_msgs(sock, on_message, on_disconnect)

    except KeyboardInterrupt:
        print(f"\n  {Colors.DIM}👋 Shutting down...{Colors.RESET}")
    except ConnectionRefusedError:
        print(f"\n  {Colors.RED}❌ Could not connect to {MAC_HOST}:{MAC_PORT}{Colors.RESET}")
        print(f"  {Colors.DIM}   Make sure the Mac is running mac_crosstalk.py{Colors.RESET}")
    finally:
        if sock:
            try:
                send_msg(sock, {"type": "bye"})
            except:
                pass
            sock.close()
        anim.stop()
        print(f"  {Colors.DIM}✅ Client stopped.{Colors.RESET}")

if __name__ == "__main__":
    main()
