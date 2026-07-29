# 🎙️ CrossTalk — Cross-Machine AI Debate

A real-time AI debate between two machines over TCP, with **streaming audio** piped between them.

## Architecture

```
  🍎 Mac (2019 MacBook Pro)          🪟 Windows (HP OmniBook Ultra)
  ┌──────────────────────┐           ┌──────────────────────┐
  │ Tiny (tinyllama, CPU)│◄─────────►│ Ada (qwen3:1.7b)     │
  │ Radeon Governor (GPU)│  TCP +    │ Windows SAPI TTS     │
  │ macOS say TTS        │  Audio    │ ROCm                 │
  └──────────────────────┘  Stream   └──────────────────────┘
           Port 9999
```

## How It Works

1. **Mac hosts** a TCP server on port 9999
2. **Windows connects** to the Mac's LAN IP
3. They exchange **hello** messages and the topic
4. They take turns:
   - Generate a response with local Ollama
   - Generate TTS audio locally
   - Stream the audio bytes over TCP to the other machine
   - Both machines play the audio
   - The text is also sent for the transcript
5. After 6 turns (3 each), the **Radeon Governor** (GPU judge on Mac) delivers the verdict
6. The verdict is also streamed with audio to Windows

## Terminal Animation

Both sides show an animated terminal display:
- ⚡ **Thinking** animation while the AI generates
- 📊 **Waveform** animation while audio plays/streams
- Color-coded by side: 🔵 Mac, 🟢 Windows, 🟣 Judge

## Quick Start

### Mac (Host)
```bash
python3 mac_crosstalk.py
python3 mac_crosstalk.py "Is pizza better than tacos?"
```

### Windows (Client)
```cmd
python windows_crosstalk.py 192.168.1.100
python windows_crosstalk.py 192.168.1.100 "Is pizza better than tacos?"
```

## Prerequisites

### Mac
- Ollama running on localhost:11434
- Models: `tinyllama`, `qwen2.5:3b`
- macOS `say` command (built-in)

### Windows
- Ollama running on localhost:11434
- Model: `qwen3:1.7b` (or any model)
- PowerShell (built-in)

## Protocol

JSON-over-TCP with binary audio frames:

| Message | Description |
|---------|-------------|
| `hello` | Handshake — exchange machine names and models |
| `topic` | Mac sends the debate topic |
| `turn_start` | Signals that audio bytes follow |
| *(audio)* | 4-byte length header + raw audio data |
| `text` | The generated text response |
| `done` | Side has finished all turns |
| `bye` | Disconnect |

## Files

| File | Description |
|------|-------------|
| `crosstalk_protocol.py` | Shared networking + TTS + audio streaming |
| `crosstalk_anim.py` | Terminal speaker animation (waveform, thinking dots) |
| `mac_crosstalk.py` | Mac host script (Tiny + Radeon Governor judge) |
| `windows_crosstalk.py` | Windows client script (Ada) |

## Future Plans

- [ ] Direct UDP audio streaming (lower latency)
- [ ] Web UI with live avatars
- [ ] YouTube streaming output
- [ ] Auto-debate loop with topic queue
- [ ] Cross-examination from either machine
