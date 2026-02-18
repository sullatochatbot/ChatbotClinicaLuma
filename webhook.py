from flask import Flask, request
import os, requests
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

if not ACCESS_TOKEN:
    print("❌ ACCESS_TOKEN não definido")
if not PHONE_NUMBER_ID:
    print("❌ PHONE_NUMBER_ID não definido")

# ============================================================
# TIMEZONE BRASIL
# ============================================================

TZ_BR = timezone(timedelta(hours=-3))

def hora_sp():
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# ============================================================
# NORMALIZA DROPBOX (ROBUSTO)
# ============================================================

def normalizar_dropbox(url):
    if not url:
        return ""
    u = url.strip()

    if "dropbox.com" in u:
        u = u.replace("www.dropbox.com", "dl.dropboxusercontent.com")
        u = u.replace("?dl=0", "")
        u = u.split("?")[0]

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
# POLÍTICA / TERMOS / DELETE (META)
# ============================================================

@app.route("/privacy", methods=["GET"])
def privacy():
    return """
    <h1>Política de Privacidade - Clínica Luma</h1>
    <p>Utilizamos o WhatsApp exclusivamente para comunicação com pacientes.</p>
    <p>Dados usados apenas para agendamento e atendimento.</p>
    <p>Contato: sol@sullato.com.br</p>
    """, 200


@app.route("/terms", methods=["GET"])
def terms():
    return "<h1>Termos de Serviço - Clínica Luma</h1>", 200


@app.route("/delete-data", methods=["GET"])
def delete_data():
    return "<h1>Solicite exclusão via sol@sullato.com.br</h1>", 200


# ============================================================
# ENVIO TEMPLATE
# ============================================================

def enviar_template_clinica(numero, template_name, imagem_url, body_params=None):

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    imagem_final = normalizar_dropbox(imagem_url)

    components = []

    if imagem_final:
        components.append({
            "type": "header",
            "parameters": [{
                "type": "image",
                "image": {"link": imagem_final}
            }]
        })

    if body_params:
        components.append({
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(p)} for p in body_params
            ]
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "pt_BR"},
            "components": components
        }
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)

    print(f"[{hora_sp()}] 📤 TEMPLATE STATUS:", r.status_code)
    print(r.text)

    return r.status_code


# ============================================================
# CONTROLE DUPLICIDADE
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
# WEBHOOK
# ============================================================

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    # ================= VERIFICAÇÃO META =================
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            print(f"[{hora_sp()}] ✅ Webhook verificado")
            return challenge, 200

        return "Token inválido", 403

    # ================= EVENTOS =================
    try:
        data = request.get_json(force=True, silent=True) or {}

        # ===== DISPARO VIA APPS SCRIPT =====
        if data.get("origem") == "apps_script_disparo":

            numero = data.get("numero")
            template_name = data.get("template")
            imagem_url = data.get("imagem_url")

            if numero and template_name and imagem_url:
                enviar_template_clinica(numero, template_name, imagem_url)
                return "OK", 200
            else:
                return "ERRO DADOS DISPARO", 400

        # ===== EVENTOS NORMAIS =====
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):

                value = change.get("value", {})
                messages = value.get("messages")
                contacts = value.get("contacts")

                if not messages or not contacts:
                    continue

                msg = messages[0]
                message_id = msg.get("id")

                if not message_id or message_id in PROCESSED_MESSAGE_IDS:
                    continue

                _mark_processed([message_id])

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
# TESTE TEMPLATE IMAGEM
# ============================================================

@app.route("/teste_template", methods=["GET"])
def teste_template():

    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": "5511988780161",
        "type": "template",
        "template": {
            "name": "luma_img_v2",
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {
                                "link": "https://dl.dropboxusercontent.com/scl/fi/o7sd6nm3cpitkpbwi6h16/Post-4_01.jpg"
                            }
                        }
                    ]
                },
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": "Anderson"
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

    r = requests.post(url, json=payload, headers=headers)

    return r.text, r.status_code


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    print(f"[{hora_sp()}] 🚀 Flask iniciado")
    app.run(host="0.0.0.0", port=5000)
