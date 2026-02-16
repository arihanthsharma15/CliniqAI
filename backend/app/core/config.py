from pydantic_settings import BaseSettings
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_name: str = "AI Clinic Call Assistant"
    environment: str = "dev"

    # Must come from .env
    database_url: str

    public_base_url: str = "http://localhost:8000"

    # Demo mode limits call length for testing
    demo_mode: bool = True
    max_demo_turns: int = 6
    skip_name_confirmation: bool = True

    # Redis for persistent job queue (optional, add later)
    redis_url: str = "redis://localhost:6379"

    # Twilio
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
    clinic_staff_number: str | None = None
    clinic_doctor_number: str | None = None
    hold_music_url: str | None = None
    staff_notify_numbers: str | None = None
    doctor_notify_numbers: str | None = None

    # AI services
    llm_provider: str = "auto"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    deepgram_api_key: str | None = None
    stt_provider: str = "twilio"
    twilio_speech_language: str = "en-US"
    twilio_speech_model: str = "phone_call"
    twilio_deepgram_speech_model: str = "deepgram_nova-2"
    twilio_speech_hints: str | None = None
    stt_fallback_empty_turns: int = 2
    stt_low_confidence_threshold: float = 0.45
    ws_brain_mode: bool = True
    ws_webhook_fallback: bool = True
    elevenlabs_api_key: str | None = None
    tts_provider: str = "twilio"
    elevenlabs_voice_id: str | None = None
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    google_tts_credentials_path: str | None = None
    google_tts_voice_name: str = "en-US-Neural2-F"
    google_tts_language_code: str = "en-US"

    class Config:
        env_file = str(ENV_PATH)


settings = Settings()
