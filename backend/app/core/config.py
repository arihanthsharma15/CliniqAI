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
    llm_provider: str = "auto"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.1-8b-instant"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    deepgram_api_key: str | None = None
    elevenlabs_api_key: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()
