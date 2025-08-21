from flask import Flask, request
import json
import os
import requests
import responder
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# === Carrega variáveis do .env ===
load_dotenv()

app = Flask(__name__)

VERIFY_TOKEN   = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN   = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID= os.getenv("PHONE_NUMBER_ID")

# === Timezone fixo São Paulo (UTC-3, sem horário de verão) ===
TZ_BR = timezone(timedelta(hours=-3))
def hora_sp() -> str:
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# === Memória simples para evitar respostas duplicadas (message.id) ===
PROCESSED_MESSAGE_IDS = set()
MAX_IDS = 500  # limite simples para não crescer infinito

def _mark_processed(ids):
    global PROCESSED_MESSAGE_IDS
    for _id in ids:
        if _id:
            PROCESSED_MESSAGE_IDS.add(_id)
    # limpeza simples
    if len(PROCESSED_MESSAGE_IDS) > MAX_IDS:
        # recria set (descarta ids antigos sem custo de tempo)
        PROCESSED_MESSAGE_IDS = set(list(PROCESSED_MESSAGE_IDS)[-MAX_IDS//2:])

# === Webhook (GET = verificação | POST = eventos) ===
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

    # POST
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"[{hora_sp()}] === RECEBIDO DO META ===")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        # Para cada entry, só processa se existir message.id novo (anti-duplicação)
        for entry in data.get("entry", []):
            try:
                changes = entry.get("changes", [])
                if not changes:
                    continue
                value = changes[0].get("value", {})
                messages = value.get("messages", [])

                # Coleta IDs das mensagens (se houver)
                incoming_ids = [m.get("id") for m in messages if isinstance(m, dict)]
                new_ids = [mid for mid in incoming_ids if mid and mid not in PROCESSED_MESSAGE_IDS]

                if not new_ids and incoming_ids:
                    print(f"[{hora_sp()}] ↩️ Evento duplicado ignorado (message.id já processado).")
                    continue

                # Marca IDs novos como processados e encaminha para o responder
                if new_ids:
                    _mark_processed(new_ids)

                # Chama o pipeline normal (responder.py)
                responder.responder_evento_mensagem(entry)

            except Exception as e:
                print(f"[{hora_sp()}] ⚠️ Erro ao processar entry: {e}")

    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro no webhook: {e}")

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
    print("📦 Payload:", json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"[{hora_sp()}] 📬 Status:", response.status_code)
        print("📨 Resposta:", response.text)
    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro ao enviar mensagem:", e)

# === Inicializa localmente ===
if __name__ == "__main__":
    print(f"[{hora_sp()}] 🚀 Servidor Flask iniciado em http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000)
