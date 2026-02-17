from flask import Flask, request, jsonify
import json, os, requests
import responder_clinica as responder
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()
app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID") or os.getenv("PHONE_NUMBER_ID")

if not VERIFY_TOKEN:
    print("⚠️ VERIFY_TOKEN não definido")
if not ACCESS_TOKEN:
    print("⚠️ WA_ACCESS_TOKEN não definido")
if not PHONE_NUMBER_ID:
    print("⚠️ WA_PHONE_NUMBER_ID não definido")

# ============================================================
# TIMEZONE BRASIL
# ============================================================

TZ_BR = timezone(timedelta(hours=-3))

def hora_sp():
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# ============================================================
# NORMALIZA DROPBOX
# ============================================================

def normalizar_dropbox(url):
    if not url:
        return ""
    u = url.strip()
    u = u.replace("https://www.dropbox.com", "https://dl.dropboxusercontent.com")
    u = u.replace("?dl=0", "")
    return u

# ============================================================
# ROTAS BÁSICAS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "CLINICA LUMA ONLINE", 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "time": hora_sp()}, 200


# ============================================================
# 🔐 POLÍTICA DE PRIVACIDADE (META OBRIGATÓRIO)
# ============================================================

@app.route("/privacy", methods=["GET"])
def privacy():
    return """
    <h1>Política de Privacidade - Clínica Luma</h1>
    <p>A Clínica Luma utiliza o WhatsApp exclusivamente para comunicação com pacientes.</p>
    <p>As informações coletadas são utilizadas apenas para agendamento e atendimento.</p>
    <p>Não compartilhamos dados com terceiros.</p>
    <p>Contato: sol@sullato.com.br</p>
    """, 200


# ============================================================
# 📜 TERMOS DE SERVIÇO (META OBRIGATÓRIO)
# ============================================================

@app.route("/terms", methods=["GET"])
def terms():
    return """
    <h1>Termos de Serviço - Clínica Luma</h1>
    <p>Este chatbot é utilizado exclusivamente para comunicação entre a Clínica Luma e seus pacientes.</p>
    <p>O uso implica concordância com o recebimento de mensagens relacionadas a agendamentos e atendimentos.</p>
    <p>Contato: sol@sullato.com.br</p>
    """, 200


# ============================================================
# 🗑 EXCLUSÃO DE DADOS (META OBRIGATÓRIO)
# ============================================================

@app.route("/delete-data", methods=["GET"])
def delete_data():
    return """
    <h1>Solicitação de Exclusão de Dados</h1>
    <p>Para solicitar a exclusão de seus dados, envie um e-mail para:</p>
    <p><strong>sol@sullato.com.br</strong></p>
    <p>Assunto: EXCLUSÃO DE DADOS</p>
    """, 200


# ============================================================
# ENVIO TEMPLATE
# ============================================================

def enviar_template_clinica(numero, nome, template_name, imagem_url):

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    imagem_final = normalizar_dropbox(imagem_url)

    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {"link": imagem_final}
                        }
                    ]
                },
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": nome
                        }
                    ]
                }
            ]
        }
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"[{hora_sp()}] 📤 TEMPLATE:", r.status_code, r.text)
        return r.status_code
    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro ao enviar template:", e)
        return 500


# ============================================================
# CONTROLE DE DUPLICIDADE
# ============================================================

PROCESSED_MESSAGE_IDS = set()
MAX_IDS = 500

def _mark_processed(ids):
    global PROCESSED_MESSAGE_IDS
    for _id in ids:
        if _id:
            PROCESSED_MESSAGE_IDS.add(_id)
    if len(PROCESSED_MESSAGE_IDS) > MAX_IDS:
        PROCESSED_MESSAGE_IDS = set(list(PROCESSED_MESSAGE_IDS)[-MAX_IDS//2:])


# ============================================================
# WEBHOOK META
# ============================================================

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    # 🔎 VERIFICAÇÃO META
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            print(f"[{hora_sp()}] ✅ Webhook verificado")
            return challenge, 200

        return "Token inválido", 403

    # 📩 EVENTOS
    try:
        data = request.get_json(force=True, silent=True) or {}

        # 🔁 DISPARO VIA APPS SCRIPT
        if data.get("origem") == "apps_script_disparo":

            numero = data.get("numero")
            nome = data.get("nome", "Paciente")
            template_name = data.get("template")
            imagem_url = data.get("imagem_url")

            if numero and template_name and imagem_url:
                enviar_template_clinica(numero, nome, template_name, imagem_url)
                return "OK", 200
            else:
                return "ERRO DADOS DISPARO", 400

        # 📲 EVENTOS NORMAIS WHATSAPP
        for entry in data.get("entry", []):
            changes = entry.get("changes", [])
            if not changes:
                continue

            value = changes[0].get("value", {})
            contacts = value.get("contacts", [])
            messages = value.get("messages", [])

            for msg in messages:

                if msg.get("type") not in ("text", "interactive"):
                    continue

                mid = msg.get("id")
                if not mid:
                    continue

                if mid in PROCESSED_MESSAGE_IDS:
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

                try:
                    responder.responder_evento_mensagem(single_entry)
                except Exception as e:
                    print(f"[{hora_sp()}] ⚠️ Erro no responder:", e)

    except Exception as e:
        print(f"[{hora_sp()}] ❌ Erro no webhook:", e)

    return "EVENT_RECEIVED", 200


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    print(f"[{hora_sp()}] 🚀 Flask iniciado")
    app.run(host="0.0.0.0", port=5000)
