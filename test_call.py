import os
from twilio.rest import Client
from dotenv import load_dotenv

# Use override=True to ensure the new .env values are picked up immediately
load_dotenv(override=True) 

# Initialize the Twilio Client
client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'),
    os.getenv('TWILIO_AUTH_TOKEN')
)

# Create the call
call = client.calls.create(
    twiml='<Response><Say voice="alice">Vera Point Alpha is now active. The plumbing is 100 percent working.</Say></Response>',
    to=os.getenv('TWILIO_PHONE_NUMBER_WIKI'),
    from_=os.getenv('TWILIO_PHONE_NUMBER') # Note the underscore in 'from_'
)

print(f"Call initiated! SID: {call.sid}")