from xml.etree.ElementTree import Element, SubElement, tostring

from h11 import Response

from app.core.config import settings


def _render(element: Element) -> str:
    return tostring(element, encoding="unicode")


def _gather_base_attrs(action_url: str, stt_mode: str | None = None) -> dict[str, str]:
    attrs: dict[str, str] = {
        "input": "speech",
        "action": action_url,
        "method": "POST",
        "actionOnEmptyResult": "true",
        "bargeIn": "true",
        "speechTimeout": "auto",
        "timeout": "2",
    }
    language = (settings.twilio_speech_language or "").strip()
    if language:
        attrs["language"] = language

    provider = (stt_mode or settings.stt_provider).strip().lower()
    if provider in {"deepgram", "twilio_deepgram"}:
        model = (settings.twilio_deepgram_speech_model or "").strip()
    else:
        model = (settings.twilio_speech_model or "").strip()
    if model:
        attrs["speechModel"] = model

    hints = (settings.twilio_speech_hints or "").strip()
    if hints:
        attrs["hints"] = hints
    return attrs


def say_response(message: str) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = message
    return _render(response)


def gather_speech(
    prompt: str,
    action_url: str,
    gather_prompt: str | None = "Please tell me how we can help.",
    stt_mode: str | None = None,
) -> str:
    response = Element("Response")
    if prompt:
        say = SubElement(response, "Say")
        say.text = prompt
    gather = SubElement(
        response,
        "Gather",
        **_gather_base_attrs(action_url, stt_mode=stt_mode),
    )
    if gather_prompt:
        say2 = SubElement(gather, "Say")
        say2.text = gather_prompt
    return _render(response)


def say_and_gather(
    message: str,
    action_url: str,
    reprompt: str | None = "You can continue speaking when ready.",
    stt_mode: str | None = None,
) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = message

    gather = SubElement(
        response,
        "Gather",
        **_gather_base_attrs(action_url, stt_mode=stt_mode),
    )
    if reprompt:
        say2 = SubElement(gather, "Say")
        say2.text = reprompt
    return _render(response)


def play_and_gather(
    audio_url: str,
    action_url: str,
    reprompt: str | None = None,
    stt_mode: str | None = None,
) -> str:
    response = Element("Response")
    gather = SubElement(
        response,
        "Gather",
        **_gather_base_attrs(action_url, stt_mode=stt_mode),
    )
    play = SubElement(gather, "Play")
    play.text = audio_url
    if reprompt:
        say2 = SubElement(gather, "Say")
        say2.text = reprompt
    return _render(response)


def start_stream_and_gather(
    message: str,
    stream_url: str,
    action_url: str,
    stt_mode: str | None = None,
    reprompt: str | None = None,
) -> str:
    response = Element("Response")
    start = SubElement(response, "Start")
    stream = SubElement(start, "Stream", url=stream_url)
    stream.set("track", "inbound_track")
    say = SubElement(response, "Say")
    say.text = message
    gather = SubElement(
        response,
        "Gather",
        **_gather_base_attrs(action_url, stt_mode=stt_mode),
    )
    if reprompt:
        say2 = SubElement(gather, "Say")
        say2.text = reprompt
    return _render(response)


def say_and_hangup(message: str) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = message
    SubElement(response, "Hangup")
    return _render(response)


def play_and_hangup(audio_url: str) -> str:
    response = Element("Response")
    play = SubElement(response, "Play")
    play.text = audio_url
    SubElement(response, "Hangup")
    return _render(response)


def hold_then_hangup(message: str, hold_seconds: int = 8) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = message

    hold_prompt = SubElement(response, "Say")
    hold_prompt.text = "Please hold while I connect you to our clinic staff."
    SubElement(response, "Pause", length=str(max(1, min(hold_seconds, 60))))

    fallback = SubElement(response, "Say")
    fallback.text = "Our team will call you back shortly. Thank you."
    SubElement(response, "Hangup")
    return _render(response)


def hold_and_dial(
    message: str,
    target_number: str,
    hold_music_url: str | None = None,
    message_audio_url: str | None = None,
    fallback_message: str = "Our team will call you back shortly. Thank you.",
) -> str:
    response = Element("Response")

    if message_audio_url:
        play_msg = SubElement(response, "Play")
        play_msg.text = message_audio_url
    else:
        say = SubElement(response, "Say")
        say.text = message

    if hold_music_url:
        play_hold = SubElement(response, "Play")
        play_hold.text = hold_music_url

    dial = SubElement(response, "Dial", timeout="20", answerOnBridge="true")
    number = SubElement(dial, "Number")
    number.text = target_number

    fallback = SubElement(response, "Say")
    fallback.text = fallback_message
    SubElement(response, "Hangup")
    return _render(response)