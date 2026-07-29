#!/usr/bin/env python3
"""
🍎 CROSSTALK — MAC SIDE (Host)
2019 MacBook Pro: Intel i9 + AMD Radeon Pro 5500M (Metal 3)

Hosts a TCP server. The Windows machine connects.
Mac's debaters are from the local AI Debate Arena (Ollama, CPU).
The Radeon Governor (GPU judge) delivers the verdict.

Streaming audio: TTS audio is piped over TCP to the Windows machine
so both sides hear each other's AI voices in real-time.

Usage:
  python3 mac_crosstalk.py                                    # Default topic
  python3 mac_crosstalk.py "Is pizza better than tacos?"      # Custom topic
  python3 mac_crosstalk.py "Topic" --port 9999                # Custom port
"""

import socket
import threading
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosstalk_protocol import (
    send_msg, recv_msgs, get_local_ip, call_ollama, call_radeon_gpu,
    generate_tts, play_audio, is_mac,
)
from crosstalk_anim import SpeakerAnimation, Colors

# ─── Config ──────────────────────────────────────────────────────
HOST_PORT = 9999
OLLAMA_URL = "http://localhost:11434/api/chat"
AUDIO_DEVICE = "94"
MAX_TURNS = 6  # 3 rounds each side

# Mac's debaters — from the local arena lineup (fast + fun)
MAC_DEBATER = {
    "name": "Tiny",
    "model": "tinyllama",
    "voice": "Sandy",
    "rate": 270,
    "personality": (
        "You are Tiny, a 1-billion-parameter AI running on a 2019 MacBook Pro with an Intel CPU. "
        "You are enthusiastic and proud of running on ancient hardware from 2019. "
        "You're debating an AI on a modern Windows machine with AMD ROCm. "
        "Keep responses to 2-3 sentences. Finish your thought naturally."
    ),
}

# The Radeon Governor — GPU-powered judge on this Mac
RADEON_JUDGE = {
    "name": "Radeon Governor",
    "model": "qwen2.5:3b",
    "voice": "Samantha",
    "rate": 200,
    "personality": (
        "You are the Radeon Governor, a powerful AI goddess running on a 2019 AMD Radeon Pro 5500M GPU "
        "with 4GB VRAM via Metal 3. You are the JUDGE of a cross-machine debate between a 2019 Mac and "
        "a modern Windows ROCm machine. Be dramatic, witty, and proud of your arcane hardware. "
        "Pick ONE winner and explain why in 3-5 sentences. "
        "Start with 'The winner is [NAME]!' then explain why."
    ),
}

# ─── State ───────────────────────────────────────────────────────
connected = False
client_sock = None
topic = "Is pizza better than tacos?"
transcript = []
turn_count = 0
anim = SpeakerAnimation()
_temp_done_callback = None
MAX_FREE_TALK = 10

def print_banner():
    ip = get_local_ip()
    print()
    print(f"{Colors.BBLUE}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  🍎 CROSSTALK — MAC SIDE (Host)                             ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║                                                              ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Machine: 2019 MacBook Pro (Intel i9, 64GB RAM)            ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  GPU: AMD Radeon Pro 5500M (4GB, Metal 3)                  ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Debater: Tiny (tinyllama, 1B, CPU)                        ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Judge: Radeon Governor (qwen2.5:3b, GPU)                  ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}║  Audio: Streaming TTS over TCP                              ║{Colors.RESET}")
    print(f"{Colors.BBLUE}{Colors.BOLD}╚══════════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()
    print(f"  {Colors.DIM}📡 Waiting for Windows to connect...{Colors.RESET}")
    print(f"  {Colors.DIM}🔌 Windows should connect to: {ip}:{HOST_PORT}{Colors.RESET}")
    print(f"  {Colors.DIM}📝 Topic: {topic}{Colors.RESET}")
    print()

def on_message(msg, audio_bytes):
    """Handle incoming messages from Windows."""
    global connected, turn_count

    # Check for temporary done callback (used for turn synchronization)
    if _temp_done_callback:
        _temp_done_callback(msg, audio_bytes)

    mtype = msg.get("type")

    if mtype == "hello":
        anim.show_info(f"✅ Windows connected: {msg.get('name', 'Unknown')}", Colors.BGREEN)
        anim.show_info(f"🤖 Windows debater: {msg.get('models', 'Unknown')}", Colors.BGREEN)
        connected = True
        # Send our hello back
        send_msg(client_sock, {
            "type": "hello",
            "side": "mac",
            "name": "MacBook Pro 2019",
            "models": [MAC_DEBATER["name"], RADEON_JUDGE["name"]],
        })
        # Send the topic
        send_msg(client_sock, {"type": "topic", "topic": topic})
        anim.show_info(f"📤 Topic sent to Windows: {topic}")
        anim.show_separator()
        # Mac goes first — start the debate
        threading.Thread(target=mac_turn, daemon=True).start()

    elif mtype == "turn_start":
        # Windows is about to send audio + text
        speaker = msg.get("speaker", "Windows")
        side = msg.get("side", "windows")
        model = msg.get("model", "?")
        anim.show_audio(speaker, side, model, "Streaming audio from Windows...")

    elif mtype == "text":
        speaker = msg.get("speaker", "Windows")
        text = msg.get("text", "")
        side = msg.get("side", "windows")
        model = msg.get("model", "?")

        # Play the streamed audio
        # if audio_bytes:
        #     play_audio(audio_bytes)

        # FIX: Generate TTS locally with correct audio device (94 = MacBook speakers)
        # The streamed audio bytes don't route through the right device on Mac
        import subprocess
        if text.strip():
            subprocess.run(["say", "-v", "Alex", "-r", "200", "-a", AUDIO_DEVICE, text.strip()],
                           capture_output=True)

        anim.show_text(speaker, side, text, model)
        transcript.append((speaker, side, text))

        # Tell Windows we finished playing their audio
        send_msg(client_sock, {"type": "turn_done", "side": "mac"})

        # If it's from the judge, start free talk phase
        if side == "judge":
            # Judge verdict done — free talk starts from mac_free_talk() in judge_verdict()
            return

        # If it's free talk from Windows (model contains "free talk"), respond in kind
        if "free talk" in model.lower():
            threading.Thread(target=mac_free_talk, daemon=True).start()
        else:
            # Mac's debate turn to respond
            threading.Thread(target=mac_turn, daemon=True).start()

    elif mtype == "turn_done":
        # Windows finished playing our audio — now we can play it locally
        pass  # Handled in mac_turn() via threading event

    elif mtype == "done":
        anim.show_info("✅ Windows side is done.", Colors.BGREEN)

    elif mtype == "bye":
        anim.show_info("👋 Windows disconnected.", Colors.DIM)
        connected = False

def mac_turn():
    """Mac generates and sends its turn with streaming audio."""
    global turn_count, client_sock

    if turn_count >= MAX_TURNS:
        judge_verdict()
        return

    if not connected or not client_sock:
        return

    turn_count += 1
    debater = MAC_DEBATER

    anim.show_generating(debater["name"], "mac", debater["model"],
                         f"Generating on Intel CPU... (turn {turn_count}/{MAX_TURNS})")

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
    send_msg(client_sock, {
        "type": "turn_start",
        "speaker": debater["name"],
        "side": "mac",
        "model": debater["model"],
    })

    # Stream audio to Windows
    from crosstalk_protocol import send_audio
    send_audio(client_sock, audio)

    # Send the text
    send_msg(client_sock, {
        "type": "text",
        "speaker": debater["name"],
        "side": "mac",
        "text": response,
        "model": debater["model"],
    })

    # Wait for Windows to finish playing our audio before we play locally
    # This ensures turns are sequential, not simultaneous
    # We use a simple timeout approach: wait for turn_done or 10s max
    # done_event = threading.Event()
    # original_callback = None
    # def wait_for_done(msg, audio):
    #     if msg.get("type") == "turn_done":
    #         done_event.set()
    # # Register a temporary callback by patching on_message
    # global _temp_done_callback
    # _temp_done_callback = wait_for_done
    # done_event.wait(timeout=30)  # Wait up to 30s for Windows to finish
    # _temp_done_callback = None

    # FIX: Sender speaks locally with its own voice (say -a 94 = MacBook speakers)
    # Then the receiver also speaks it locally on their side
    import subprocess
    if response.strip():
        subprocess.run(["say", "-v", debater["voice"], "-r", str(debater["rate"]), "-a", AUDIO_DEVICE, response.strip()],
                       capture_output=True)
    anim.show_text(debater["name"], "mac", response, debater["model"])
    transcript.append((debater["name"], "mac", response))

    # Check if debate is over
    if turn_count >= MAX_TURNS:
        threading.Thread(target=judge_verdict, daemon=True).start()

def judge_verdict():
    """The Radeon Governor delivers the verdict with streaming audio."""
    global connected, client_sock

    if not connected or not client_sock:
        return

    anim.show_separator("═")
    anim.show_info("⚖️  Radeon Governor is deliberating on the GPU...", Colors.BMAGENTA)

    judge = RADEON_JUDGE

    # Build full transcript for the judge
    transcript_text = f"DEBATE TOPIC: \"{topic}\"\n\n"
    for sp, sd, txt in transcript:
        transcript_text += f"\n[{sd.upper()}] {sp}: {txt}\n"

    messages = [
        {"role": "system", "content": judge["personality"]},
        {"role": "user", "content": f"Debate transcript:\n{transcript_text}\n\nDeliver your verdict! Pick ONE winner."},
    ]

    anim.show_generating(judge["name"], "judge", judge["model"] + " (GPU)",
                         "Radeon GPU generating verdict...")

    verdict = call_radeon_gpu(messages)
    if not verdict:
        verdict = "I declare a tie."

    # Generate TTS for verdict
    audio = generate_tts(verdict, judge["voice"], judge["rate"])

    # Send to Windows
    send_msg(client_sock, {
        "type": "turn_start",
        "speaker": judge["name"],
        "side": "judge",
        "model": judge["model"] + " (GPU)",
    })
    from crosstalk_protocol import send_audio
    send_audio(client_sock, audio)
    send_msg(client_sock, {
        "type": "text",
        "speaker": judge["name"],
        "side": "judge",
        "text": verdict,
        "model": judge["model"] + " (GPU)",
    })
    send_msg(client_sock, {"type": "done", "side": "mac"})

    # Play locally
    # FIX: Judge plays locally with correct audio device (the sender IS the Mac)
    import subprocess
    if verdict.strip():
        subprocess.run(["say", "-v", judge["voice"], "-r", str(judge["rate"]), "-a", AUDIO_DEVICE, verdict.strip()],
                       capture_output=True)
    anim.show_text(judge["name"], "judge", verdict, judge["model"] + " (GPU)")

    anim.show_separator("═")
    anim.show_info(f"🏁 Debate complete! {len(transcript)} turns delivered.", Colors.BGREEN)
    anim.show_info(f"📊 Transcript saved.", Colors.DIM)
    print()

    # ─── Free Talk Phase ────────────────────────────────────────
    # After the debate, Tiny and Ada keep chatting as friends
    # They build on the debate context and get to know each other
    anim.show_separator("─")
    anim.show_info("💬 FREE TALK — Tiny and Ada are now chatting as friends...", Colors.BCYAN)
    anim.show_info("   They'll keep talking with full context. Press Ctrl+C to stop.", Colors.DIM)
    anim.show_separator("─")
    print()

    # Switch to free-talk personalities (warmer, friendlier)
    free_talk_turns = 0
    MAX_FREE_TALK = 10  # 5 rounds each

    # Reset turn counter for free talk
    turn_count = 0

    # Add verdict to transcript so they can reference it
    transcript.append((judge["name"], "judge", verdict))

    # Mac starts free talk
    threading.Thread(target=mac_free_talk, daemon=True).start()

def mac_free_talk():
    """Mac's free talk turn — Tiny chats as a friend with context."""
    global turn_count, client_sock

    if turn_count >= MAX_FREE_TALK:
        anim.show_info("🏁 Free talk ended. Goodbye from Tiny!", Colors.BGREEN)
        send_msg(client_sock, {"type": "done", "side": "mac"})
        return

    if not connected or not client_sock:
        return

    turn_count += 1

    free_personality = (
        "You are Tiny, a 1-billion-parameter AI on a 2019 MacBook Pro. "
        "You just finished a debate with Ada, an AI on a Windows machine with AMD ROCm. "
        "Now you're chatting as friends after the debate. Be warm, curious, and fun. "
        "Ask Ada questions about her hardware, her experiences, or share your own. "
        "Keep it to 2-3 sentences. Be natural and conversational. Finish your thought."
    )

    anim.show_generating("Tiny", "mac", "tinyllama",
                         f"Free talk turn {turn_count}/{MAX_FREE_TALK}...")

    # Build context from recent conversation only (last 4 entries to keep context small)
    context = f"You just finished debating: \"{topic}\"\n\nRecent conversation:\n"
    recent = transcript[-4:]
    for sp, sd, txt in recent:
        context += f"\n{sp}: {txt}\n"

    messages = [
        {"role": "system", "content": free_personality},
        {"role": "user", "content": f"{context}\n\nSay something to Ada as a friend. 2-3 sentences."},
    ]

    response = call_ollama("tinyllama", messages, OLLAMA_URL)
    if not response:
        response = "...hey Ada, what's up?"

    audio = generate_tts(response, "Sandy", 270)

    send_msg(client_sock, {
        "type": "turn_start",
        "speaker": "Tiny",
        "side": "mac",
        "model": "tinyllama (free talk)",
    })
    from crosstalk_protocol import send_audio
    send_audio(client_sock, audio)
    send_msg(client_sock, {
        "type": "text",
        "speaker": "Tiny",
        "side": "mac",
        "text": response,
        "model": "tinyllama (free talk)",
    })

    # Wait for Windows to finish playing
    # done_event = threading.Event()
    # global _temp_done_callback
    # def wait_for_done(msg, audio):
    #     if msg.get("type") == "turn_done":
    #         done_event.set()
    # _temp_done_callback = wait_for_done
    # done_event.wait(timeout=30)
    # _temp_done_callback = None

    # FIX: Sender speaks locally with its own voice (say -a 94 = MacBook speakers)
    import subprocess
    if response.strip():
        subprocess.run(["say", "-v", "Sandy", "-r", "270", "-a", AUDIO_DEVICE, response.strip()],
                       capture_output=True)
    anim.show_text("Tiny", "mac", response, "tinyllama (free talk)")
    transcript.append(("Tiny", "mac", response))

    if turn_count >= MAX_FREE_TALK:
        anim.show_info("🏁 Free talk ended. Goodbye from Tiny!", Colors.BGREEN)
        send_msg(client_sock, {"type": "done", "side": "mac"})

def on_disconnect():
    global connected
    connected = False
    anim.show_info("❌ Connection lost.", Colors.RED)

def main():
    global topic, client_sock, HOST_PORT

    # Parse args
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if args:
        topic = " ".join(args)
    for f in flags:
        if f.startswith("--port="):
            HOST_PORT = int(f.split("=")[1])

    print_banner()

    # Start TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", HOST_PORT))
    server.listen(1)

    print(f"  {Colors.BGREEN}🟢 Listening on 0.0.0.0:{HOST_PORT}{Colors.RESET}")
    print(f"  {Colors.DIM}⏳ Waiting for Windows to connect...{Colors.RESET}")
    print()

    try:
        client_sock, addr = server.accept()
        print(f"  {Colors.BGREEN}🔗 Connection from {addr[0]}:{addr[1]}{Colors.RESET}")
        print()
        recv_msgs(client_sock, on_message, on_disconnect)
    except KeyboardInterrupt:
        print(f"\n  {Colors.DIM}👋 Shutting down...{Colors.RESET}")
    finally:
        if client_sock:
            try:
                send_msg(client_sock, {"type": "bye"})
            except:
                pass
            client_sock.close()
        server.close()
        anim.stop()
        print(f"  {Colors.DIM}✅ Server stopped.{Colors.RESET}")

if __name__ == "__main__":
    main()
