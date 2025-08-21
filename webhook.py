from flask import Flask, request
import json, os, requests
import responder
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# === Env ===
load_dotenv()
app = Flask(__name__)

VERIFY_TOKEN    = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN    = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# === Timezone SP (UTC-3) ===
TZ_BR = timezone(timedelta(hours=-3))
def hora_sp() -> str:
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# === Anti-duplicação por message.id ===
PROCESSED_MESSAGE_IDS = set()
MAX_IDS = 500
def _mark_processed(ids):
    global PROCESSED_MESSAGE_IDS
    for _id in ids:
        if _id:
            PROCESSED_MESSAGE_IDS.add(_id)
    if len(PROCESSED_MESSAGE_IDS) > MAX_IDS:
        PROCESSED_MESSAGE_IDS = set(list(PROCESSED_MESSAGE_IDS)[-MAX_IDS//2:])

# === Healthcheck ===
@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "time": hora_sp()}, 200

# === GET: verificação webhook ===
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        print(f"[{hora_sp()}] 📥 Verificação recebida:", mode)
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print(f"[{hora_sp()}] ✅ Webhook verificado")
            return challenge, 200
        print(f"[{hora_sp()}] ❌ Token inválido:", token)
        return "Token inválido", 403
    # POST: eventos
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"[{hora_sp()}] === RECEBIDO DO META ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        for entry in data.get("entry", []):
            changes = entry.get("changes", [])
            if not changes:
                continue
            value = changes[0].get("value", {})
            contacts = value.get("contacts", [])
            messages = value.get("messages", [])

            # Processa UMA message por vez (evita saudar 2x)
            for msg in messages:
                if msg.get("type") not in ("text", "interactive"):
                    continue
                mid = msg.get("id")
                if not mid:
                    continue
                if mid in PROCESSED_MESSAGE_IDS:
                    print(f"[{hora_sp()}] ↩️ Duplicado ignorado: {mid}")
                    continue
                _mark_processed([mid])

                single_entry = {
                    "changes": [{
                        "value": {
                            "messages": [msg],
                            "contacts": contacts
                        }
                    }]
                }
                responder.responder_evento_mensagem(single_entry)

    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro no webhook: {e}")

    return "EVENT_RECEIVED", 200

# === Envio manual (opcional) ===
def send_text_message(phone_number, message):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp","to":phone_number,"type":"text","text":{"body":message}}
    print(f"[{hora_sp()}] 📤 Enviando manual...")
    try:
        r = requests.post(url, headers=headers, json=payload)
        print(f"[{hora_sp()}] 📬 Status:", r.status_code, r.text)
    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro ao enviar:", e)

if __name__ == "__main__":
    print(f"[{hora_sp()}] 🚀 Flask em http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000)
