from xml.etree.ElementTree import Element, SubElement, tostring


def _render(element: Element) -> str:
    return tostring(element, encoding="unicode")


def say_response(message: str) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = message
    return _render(response)


def gather_speech(prompt: str, action_url: str) -> str:
    response = Element("Response")
    say = SubElement(response, "Say")
    say.text = prompt
    gather = SubElement(response, "Gather", input="speech", action=action_url, method="POST")
    say2 = SubElement(gather, "Say")
    say2.text = "Please tell me how we can help."
    return _render(response)
