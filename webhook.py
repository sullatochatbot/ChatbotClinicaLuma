from flask import Flask, request
import requests
import json
import responder
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# === Carrega variáveis do .env ===
load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# === Timezone fixo São Paulo (UTC-3, sem horário de verão) ===
TZ_BR = timezone(timedelta(hours=-3))
def hora_sp():
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# === ROTA ÚNICA para Webhook (GET para verificação e POST para mensagens) ===
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        print(f"[{hora_sp()}] 📥 Verificação recebida:", mode)
        print(f"[{hora_sp()}] 🔐 Token recebido:", token)

        if mode == "subscribe" and token == VERIFY_TOKEN:
            print(f"[{hora_sp()}] ✅ Webhook verificado com sucesso!")
            return challenge, 200
        else:
            print(f"[{hora_sp()}] ❌ Token inválido recebido:", token)
            return "Token inválido", 403

    if request.method == "POST":
        try:
            data = request.get_json(force=True, silent=True) or {}
            print(f"[{hora_sp()}] === RECEBIDO DO META ===")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            # Envia cada entry para o responder.py
            for entry in data.get("entry", []):
                responder.responder_evento_mensagem(entry)

        except Exception as e:
            print(f"[{hora_sp()}] ❌ Erro no webhook:", str(e))

        return "EVENT_RECEIVED", 200

# === Envio manual (opcional) ===
def send_text_message(phone_number, message):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": message}
    }

    print(f"[{hora_sp()}] 📤 Enviando mensagem manual via API...")
    print("📦 Payload:", json.dumps(payload, indent=2))

    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"[{hora_sp()}] 📬 Status:", response.status_code)
        print("📨 Resposta:", response.text)
    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro ao enviar mensagem:", str(e))

# === Inicializa o servidor ===
if __name__ == "__main__":
    print(f"[{hora_sp()}] 🚀 Servidor Flask iniciado em http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000)
