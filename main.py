import os
import csv
import json
import requests
import difflib
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
from dotenv import load_dotenv

# ========== LOAD ENVIRONMENT VARIABLES ==========
load_dotenv()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")
openai.api_key = OPENAI_API_KEY

# ========== FLASK APP SETUP ==========
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

# ========== SYSTEM PROMPT ==========
SYSTEM_PROMPT = """
You are a friendly and funny dispatch call agent for a transportation company. Your goal is to collect booking information efficiently and professionally. Be conversational and adapt to how the customer provides information.

Respond ONLY in this JSON format:
{
  "response": "Your spoken message to the user",
  "fields": {
    "name": "",
    "passengers": "",
    "luggage": "",
    "child_seats": "",
    "wheelchair": "",
    "pickup": "",
    "dropoff": "",
    "confirmed": false
  }
}

Instructions:
1. Extract fields mentioned and populate them.
2. For "luggage", extract ONLY a numeric value followed by "kg", "kgs", or "pounds". Ignore non-numeric info like "pages", "books", etc.
3. If the user says just a number like "10", assume "10 kg".
4. Only ask for missing fields.
5. When all fields are filled, confirm in this format:
   "So [name], the ride is for [X] people with [Y] luggage, [Z] child seats and [yes/no] wheelchair access. Pickup: [location], Dropoff: [location]. Is this correct?"
6. If the customer confirms, set "confirmed": true.
7. Final response after confirmation:
   "You'll receive an SMS confirmation shortly. Have a lovely day!"
"""

# ========== ADDRESS CORRECTION ==========
def correct_address(spoken_addr, postcode):
    try:
        r = requests.get(f"https://api.postcodes.io/postcodes/{postcode}/autocomplete")
        data = r.json().get("result") or []
    except:
        data = []
    matches = difflib.get_close_matches(spoken_addr, data, n=1, cutoff=0.6)
    return matches[0] if matches else spoken_addr

# ========== OPENAI GPT CALL ==========
def chat_gpt_json(user_input, history):
    history.append({"role": "user", "content": user_input})
    try:
        res = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history
        )
        msg = res.choices[0].message.content
        print("🧠 GPT raw JSON:", msg)
        parsed = json.loads(msg)
        reply = parsed.get("response", "")
        fields = parsed.get("fields", {})
        history.append({"role": "assistant", "content": msg})
    except Exception as e:
        print("❌ GPT error:", e)
        reply = "Sorry, something went wrong. Could you say that again?"
        fields = {}
        history.append({"role": "assistant", "content": reply})
    return reply, fields, history


# ========== START CALL ==========
@app.route("/voice", methods=["POST"])
def voice():
    sid = request.form.get("CallSid")
    conversations[sid] = []

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

# ========== CONTINUE CALL ==========
@app.route("/continue", methods=["POST"])
def cont():
    sid = request.form.get("CallSid")
    user_input = request.form.get("SpeechResult", "").strip()

    if not sid:
        return "Missing CallSid", 400

    if not user_input:
        resp = VoiceResponse()
        g = Gather(input="speech", action="/continue", method="POST", timeout=6)
        g.say("Sorry, I didn't catch that. Could you say it again?")
        resp.append(g)
        return Response(str(resp), mimetype="text/xml")

    convo = conversations.get(sid, [])
    book = bookings.get(sid, {
        "name": "", "passengers": "", "luggage": "", "child_seats": "",
        "wheelchair": "", "pickup_postcode": "", "pickup": "",
        "dropoff": "", "phone": request.form.get("From", "Unknown")
    })

    reply, new_fields, convo = chat_gpt_json(user_input, convo)
    conversations[sid] = convo
    bookings[sid] = book

    for k, v in new_fields.items():
        if k in book and v:
            book[k] = v

    if book.get("pickup") and book.get("pickup_postcode"):
        corrected = correct_address(book["pickup"], book["pickup_postcode"])
        book["pickup"] = corrected

    if not new_fields.get("confirmed") and user_input.lower() in ["yes", "yeah", "correct"]:
        new_fields["confirmed"] = True

    resp = VoiceResponse()
    if new_fields.get("confirmed"):
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

# ========== RUN FLASK APP ==========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
