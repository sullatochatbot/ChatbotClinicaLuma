import os
import requests
from flask import Flask, request
from dotenv import load_dotenv
import responder_clinica as responder

load_dotenv()
app = Flask(__name__)

# ============================================================
# CONTROLE DE DUPLICIDADE
# ============================================================

MENSAGENS_PROCESSADAS = set()

# ============================================================
# VARIÁVEIS DE AMBIENTE (PADRÃO OFICINA)
# ============================================================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN")

# ============================================================
# HOME
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "CLINICA LUMA ONLINE", 200

# ============================================================
# POLÍTICA DE PRIVACIDADE
# ============================================================

@app.route("/politica-de-privacidade", methods=["GET"])
def politica_privacidade():
    return """
    <h1>Política de Privacidade – Clínica Luma</h1>
    <p>Utilizamos dados exclusivamente para atendimento.</p>
    <p>Não compartilhamos informações com terceiros.</p>
    <p>Contato: sol@sullato.com.br</p>
    """, 200

# ============================================================
# VERIFICAÇÃO META
# ============================================================

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Erro", 403

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
# ENVIO TEMPLATE (IGUAL OFICINA)
# ============================================================

def enviar_template_clinica(numero, imagem_url):

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "template",
        "template": {
            "name": "clinica_disparo1",
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {"link": imagem_url}
                        }
                    ]
                }
            ]
        }
    }

    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=30)
    print("📤 TEMPLATE:", r.status_code, r.text)

# ============================================================
# WEBHOOK POST
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        data = request.get_json(force=True)
    except:
        data = {}

    print("📩 PAYLOAD RECEBIDO:")
    print(data)

    # ===== DISPARO APPS SCRIPT =====
    if data.get("origem") == "apps_script_disparo" or data.get("tipo") == "apps_script_disparo":

        numero = data.get("numero")
        imagem = normalizar_dropbox(data.get("imagem_url"))

        if numero and imagem:
            enviar_template_clinica(numero, imagem)
            print("🚀 DISPARO EXECUTADO")
            return "OK", 200
        else:
            return "ERRO", 400

    # ===== EVENTOS META =====
    if "entry" not in data:
        return "OK", 200

    for entry in data["entry"]:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages")
            contacts = value.get("contacts")

            if not messages or not contacts:
                continue

            msg = messages[0]

            if "from" not in msg:
                continue

            message_id = msg.get("id")

            if message_id in MENSAGENS_PROCESSADAS:
                continue

            MENSAGENS_PROCESSADAS.add(message_id)

            numero = msg.get("from") or contacts[0].get("wa_id")

            if not numero:
                print("⚠️ Número não identificado")
                continue

            nome = contacts[0].get("profile", {}).get("name", "Cliente")

            texto = ""

            # TEXTO NORMAL
            if msg.get("type") == "text":
                texto = msg.get("text", {}).get("body", "").strip()

            # INTERACTIVE
            elif msg.get("type") == "interactive":
                interactive = msg.get("interactive", {})
                tipo = interactive.get("type")

                if tipo == "button_reply":
                    texto = interactive["button_reply"].get("id") or interactive["button_reply"].get("title")

                elif tipo == "list_reply":
                    texto = interactive["list_reply"].get("id") or interactive["list_reply"].get("title")

            # BOTÃO TEMPLATE
            elif msg.get("type") == "button":
                texto = msg.get("button", {}).get("text")

            if texto and len(texto.strip()) > 0:

                print(f"👉 RECEBIDO: {texto}")
                print("📞 ENVIANDO PARA RESPONDER:", numero)

                responder.responder_evento_mensagem({
                    "changes": [{
                        "value": {
                            "messages": [msg],
                            "contacts": contacts
                        }
                    }]
                })

    return "OK", 200

# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
