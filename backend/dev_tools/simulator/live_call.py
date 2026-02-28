import requests
import re
import subprocess
from uuid import uuid4

BASE = "http://localhost:8000/api/calls"
CALL_SID = uuid4().hex

AUDIO_PATTERN = r"<Play>(.*?)</Play>"


def play_audio(url):
    print(f"\n🤖 BOT SPEAKING → {url}")
    r = requests.get(url)

    with open("bot.mp3", "wb") as f:
        f.write(r.content)

    subprocess.run(["mpg123", "bot.mp3"])


# -----------------------------
# STEP 1 — START CALL
# -----------------------------
print("\n📞 Starting Call...\n")

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

# -----------------------------
# STEP 2 — CONVERSATION LOOP
# -----------------------------
while True:
    user = input("\n👤 PATIENT: ")

    if user.lower() in ["exit", "quit"]:
        print("📞 Call Ended")
        break

    r = requests.post(
        f"{BASE}/collect",
        data={
            "CallSid": CALL_SID,
            "SpeechResult": user,
            "Confidence": 0.95,
        },
    )

    matches = re.findall(AUDIO_PATTERN, r.text)
    for url in matches:
        play_audio(url)