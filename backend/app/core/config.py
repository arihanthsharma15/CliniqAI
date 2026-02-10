from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "AI Clinic Call Assistant"
    environment: str = "dev"

    # Must come from .env
    database_url: str

    public_base_url: str = "http://localhost:8000"

    # Twilio
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None

    # AI services
    openai_api_key: str | None = None
    deepgram_api_key: str | None = None
    elevenlabs_api_key: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()
