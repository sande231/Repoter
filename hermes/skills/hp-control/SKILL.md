---
name: hp-control
description: Control Sandeep's HP laptop screen (Windows) - play YouTube videos, open websites and apps, pause/resume media, change volume.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [windows, youtube, media, desktop, personal]
    category: personal
    requires_toolsets: [terminal]
---

# HP Control

Hermes runs inside WSL Ubuntu on Sandeep's HP laptop. WSL can launch Windows
programs, so these commands show things on the HP's real Windows screen and speakers.
Always run Windows commands from /mnt/c (avoids UNC path warnings).

## When to Use
- "play <song/video> on YouTube", "put on some music"
- "open <website>" or "open <app>" on the laptop
- "pause", "resume", "next", "volume up/down", "mute"

## Play a YouTube video
1. Find the top result (title + id):
   ~/synapse/.venv/bin/yt-dlp --no-warnings --print "%(title)s | %(id)s" "ytsearch1:<search words>"
2. Open it:
   cd /mnt/c && cmd.exe /c start "" "https://www.youtube.com/watch?v=<id>"
3. Tell the user the video title that is playing.

## Open a website
   cd /mnt/c && cmd.exe /c start "" "<https url>"

## Open an app (only these)
- Chrome:     cd /mnt/c && cmd.exe /c start "" chrome
- Notepad:    cd /mnt/c && cmd.exe /c start "" notepad
- Calculator: cd /mnt/c && cmd.exe /c start "" calc
- File Explorer: cd /mnt/c && cmd.exe /c start "" explorer
- Spotify (if installed): cd /mnt/c && cmd.exe /c start "" spotify:

## Media and volume keys
   powershell.exe -NoProfile -Command "(New-Object -ComObject WScript.Shell).SendKeys([char]<code>)"
Codes: 179 = play/pause, 176 = next track, 177 = previous track,
175 = volume up, 174 = volume down, 173 = mute/unmute.
For "volume up a lot", send 175 five times.

## Rules
- Never use the built-in browser tool to play videos or music. It is headless inside WSL, so the user cannot see or hear it. Always use cmd.exe start to open media in Windows.
- Only open https websites and the apps listed above.
- Ask before closing apps, shutting down, restarting, or deleting anything. Never do those by yourself.
- Never type passwords, never log in to accounts, never install software on Windows.
- If a command fails with "cmd.exe: not found", WSL interop is off. Tell the user; do not try to fix it.
