import requests
import re
import subprocess

BASE = "http://localhost:8000/api/calls"
CALL_SID = "LOCAL_CALL_1"

AUDIO_PATTERN = r"<Play>(.*?)</Play>"


def play_audio(url):
    print(f"\n🤖 BOT SPEAKING → {url}")

    audio = requests.get(url)

    with open("bot.mp3", "wb") as f:
        f.write(audio.content)

    subprocess.run(["mpg123", "bot.mp3"])


r = requests.post(
    f"{BASE}/webhook",
    data={
        "CallSid": CALL_SID,
        "From": "+919999999999",
        "To": "+911111111111",
        "CallStatus": "ringing",
    },
)

match = re.search(AUDIO_PATTERN, r.text)

if match:
    play_audio(match.group(1))