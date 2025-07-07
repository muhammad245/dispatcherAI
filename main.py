import os
import csv
import json
import requests
import difflib
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
from dotenv import load_dotenv


# ==========
# CONFIGURATION
# ==========
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY  
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")
           

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
conversations = {}
bookings = {}

CSV_FILE = "booking.csv"
FIELDS = [
    "name", "passengers", "luggage", "child_seats", "wheelchair",
    "pickup_postcode", "pickup", "dropoff", "phone"
]

if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)

# ==========
# SYSTEM PROMPT
# ==========
SYSTEM_PROMPT = """
You are a helpful and efficient dispatch call agent. Your task is to collect booking details in a clear and friendly manner.

Always respond **only in JSON** format as shown below:

{
  "response": "Your message to the user.",
  "fields": {
    "name": "",
    "passengers": "",
    "luggage": "",
    "child_seats": "",
    "wheelchair": "",
    "pickup_postcode": "",
    "pickup": "",
    "dropoff": "",
    "confirmed": false
  }
}

Guidelines:

- For luggage, use format: "X kg" or "Y pounds".
- Ask for any missing fields one at a time, using natural and polite language.
- Once all fields are filled except `pickup_postcode`, say:
  "Thanks! What’s the pickup postcode?"
- After receiving the postcode, do **not** confirm the booking yet.
- When the user next asks to confirm, include the corrected pickup address in your response:
  "Thanks! Pickup: [corrected_address]. Is this correct?"
- Once the user confirms the corrected address, set `"confirmed": true` and say:
  "You'll receive an SMS confirmation shortly. Have a lovely day!"

Be concise, polite, and clear. Stay in JSON at all times.
"""


# ==========
# ADDRESS CORRECTION
# ==========
def correct_address(spoken_addr, postcode):
    try:
        r = requests.get(f"https://api.postcodes.io/postcodes/{postcode}/autocomplete")
        data = r.json().get("result") or []
    except:
        data = []
    matches = difflib.get_close_matches(spoken_addr, data, n=1, cutoff=0.6)
    return matches[0] if matches else spoken_addr

# ==========
# GPT CALL
# ==========
def chat_gpt_json(user_input, history):
    history.append({"role": "user", "content": user_input})
    try:
        res = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
        )
        msg = res.choices[0].message.content
        parsed = json.loads(msg)
        reply = parsed.get("response", "")
        fields = parsed.get("fields", {})
        history.append({"role": "assistant", "content": msg})
        print("🧠 GPT raw JSON:", msg)
    except Exception as e:
        print("❌ GPT error:", e)
        reply = "Sorry, something went wrong. Could you say that again?"
        fields = {}
        history.append({"role": "assistant", "content": reply})
    return reply, fields, history

# ==========
# START CALL
# ==========
@app.route("/voice", methods=["POST"])
def voice():
    sid = request.form["CallSid"]
    conversations[sid] = []

    # ✅ Initialize booking dict with all expected fields
    bookings[sid] = {
        "name": "", "passengers": "", "luggage": "", "child_seats": "",
        "wheelchair": "", "pickup_postcode": "", "pickup": "",
        "dropoff": "", "phone": request.form.get("From", "Unknown")
    }

    resp = VoiceResponse()
    gather = Gather(input="speech", action="/continue", method="POST", timeout=6)
    gather.say("Welcome! Let's book your ride. First, what's your name?")
    resp.append(gather)
    return Response(str(resp), mimetype="text/xml")

# ==========
# CONTINUE DIALOGUE
# ==========
@app.route("/continue", methods=["POST"])
def cont():
    sid = request.form["CallSid"]
    user_input = request.form.get("SpeechResult", "").strip()
    print("🗣️ User said:", user_input)

    if not user_input:
        resp = VoiceResponse()
        g = Gather(input="speech", action="/continue", method="POST", timeout=6)
        g.say("Sorry, I didn't catch that. Could you say it again?")
        resp.append(g)
        return Response(str(resp), mimetype="text/xml")

    convo = conversations.get(sid, [])
    book = bookings.get(sid)
    reply, new_fields, convo = chat_gpt_json(user_input, convo)
    conversations[sid] = convo

    # ✅ Update fields in booking
    for k, v in new_fields.items():
        if k in book and v != "":
            book[k] = v
            print(f"✅ Set {k} = {v}")

    # ✅ Autocorrect pickup address if postcode is available
    if book.get("pickup") and book.get("pickup_postcode"):
        corrected = correct_address(book["pickup"], book["pickup_postcode"])
        if corrected != book["pickup"]:
            print(f"🧹 Corrected pickup address from '{book['pickup']}' to '{corrected}'")
            book["pickup"] = corrected

    # ✅ Fallback confirmation if user says "yes"
    if not new_fields.get("confirmed") and user_input.lower() in ["yes", "yeah", "correct"]:
        new_fields["confirmed"] = True
        print("✅ Fallback confirmation triggered.")

    print("📦 Booking so far:", book)

    resp = VoiceResponse()
    if new_fields.get("confirmed"):
        print("💾 Saving to CSV...")
        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([book.get(k, "") for k in FIELDS])
        resp.say("You'll receive an SMS confirmation shortly. Have a lovely day!")
        resp.hangup()
    else:
        g = Gather(input="speech", action="/continue", method="POST", timeout=6)
        g.say(reply)
        resp.append(g)

    return Response(str(resp), mimetype="text/xml")

# ==========
# RUN APP
# ==========
if __name__ == "__main__":
    app.run(debug=True)






