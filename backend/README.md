# Backend (MVP)

## What works now
- `/api/calls/connect` creates a local call session.
- `/api/calls/transcript` stores a transcript line.
- Basic CRUD for tasks and transcripts.

## Run (local)
1. Install deps and run:

```bash
pip install -e .
uvicorn app.main:app --reload
```

## Notes
This is a local stubbed flow to prove call connect → transcript storage.
Twilio/Deepgram/OpenAI/ElevenLabs integration will be wired later.
