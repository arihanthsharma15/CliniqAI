import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]

client = Client(account_sid, auth_token)

call = client.calls.create(
    to="+919319334144",  
    from_="+18303810453", 
    url="https://kimberly-biliteral-unslowly.ngrok-free.dev/api/calls/webhook",
)

print("Call SID:", call.sid)
