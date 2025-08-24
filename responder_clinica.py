# responder_clinica.py — Fluxo final Clínica Luma
import os, re, json, requests
from datetime import datetime
from typing import Dict, Any, List, Optional

# ====== ENV do WhatsApp / Sheets ======
WA_ACCESS_TOKEN      = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID   = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
CLINICA_SHEET_ID     = os.getenv("CLINICA_SHEET_ID", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

GRAPH_URL = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages" if WA_PHONE_NUMBER_ID else ""
HEADERS   = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}

# ====== Google Sheets via gspread ======
import gspread
from google.oauth2.service_account import Credentials

def _gspread():
    if not CLINICA_SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
        raise RuntimeError("Config Sheets ausente. Defina CLINICA_SHEET_ID e GOOGLE_CREDENTIALS_JSON no .env")
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(CLINICA_SHEET_ID)
    # garante as abas/headers
    _ensure_ws(ss, "Pacientes", ["cpf","nome","nasc","endereco","forma","tipo","created_at"])
    _ensure_ws(ss, "Solicitacoes", ["timestamp","tipo","forma","cpf","nome","nasc","especialidade","exame","endereco"])
    _ensure_ws(ss, "Pesquisa", ["timestamp","cpf","nome","nasc","endereco","especialidade","exame"])
    return ss

def _ensure_ws(ss, title, headers):
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows="1000", cols=str(len(headers)+2))
        ws.insert_row(headers, index=1)
    else:
        row1 = ws.row_values(1)
        if row1 != headers:
            ws.resize(rows=max(ws.row_count, 1000), cols=len(headers))
            ws.update(f"A1:{chr(64+len(headers))}1", [headers])

def _hora_sp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S -03:00")

def _send_text(to, text):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA TEXT]", to, text); return
    payload = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"preview_url":False,"body":text[:4096]}}
    r = requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)
    if r.status_code >= 300:
        print("[WA ERROR]", r.status_code, r.text)

def _send_buttons(to, body, buttons):
    # buttons: [{"id":"op_consulta","title":"Consulta"}, ...]
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA BTNS]", to, body, buttons); return
    payload = {
        "messaging_product":"whatsapp",
        "to":to,
        "type":"interactive",
        "interactive":{
            "type":"button",
            "body":{"text": body[:1024]},
            "action":{"buttons":[{"type":"reply","reply":b} for b in buttons[:3]]}
        }
    }
    r = requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)
    if r.status_code >= 300:
        print("[WA ERROR]", r.status_code, r.text)

# ====== UI ======
WELCOME = "Bem-vindo à Clínica Luma! Escolha uma opção abaixo para começar."
BTN_ROOT = [
    {"id":"op_consulta","title":"Consulta"},
    {"id":"op_exames","title":"Exames"},
    {"id":"op_mais","title":"+ Opções"},
]
BTN_MAIS = [
    {"id":"op_retorno","title":"Retorno de consulta"},
    {"id":"op_resultado","title":"Resultado de exames"},
    {"id":"op_pesquisa","title":"Pesquisa"},
]

# ====== Validadores ======
_RE_CPF   = re.compile(r"\D")
_RE_DATE  = re.compile(r"^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/\d{4}$")
def _cpf_clean(s): return _RE_CPF.sub("", s or "")
def _date_ok(s): return bool(_RE_DATE.match(s or ""))

# ====== Sheets ops ======
def _find_paciente(ss, cpf) -> Optional[Dict[str,str]]:
    ws = ss.worksheet("Pacientes")
    col = ws.col_values(1)  # cpf
    try:
        idx = col.index(cpf) + 1
    except ValueError:
        return None
    headers = ws.row_values(1)
    row = ws.row_values(idx)
    return dict(zip(headers, row))

def _upsert_paciente(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Pacientes")
    cpf = data.get("cpf")
    if not cpf: return
    col = ws.col_values(1)
    if cpf in col:
        return
    row = [
        data.get("cpf",""), data.get("nome",""), data.get("nasc",""), data.get("endereco",""),
        data.get("forma",""), data.get("tipo",""), _hora_sp()
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_solicitacao(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Solicitacoes")
    row = [
        _hora_sp(), data.get("tipo",""), data.get("forma",""), data.get("cpf",""), data.get("nome",""),
        data.get("nasc",""), data.get("especialidade",""), data.get("exame",""), data.get("endereco","")
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_pesquisa(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Pesquisa")
    row = [_hora_sp(), data.get("cpf",""), data.get("nome",""), data.get("nasc",""),
           data.get("endereco",""), data.get("especialidade",""), data.get("exame","")]
    ws.append_row(row, value_input_option="USER_ENTERED")

# ====== Sessão em memória (simples) ======
SESS = {}  # wa_id -> {"route": str, "stage": str, "data": dict}

# Campos por fluxo
CONSULTA_FIELDS = [
    ("forma",        "Convênio ou Particular?"),
    ("nome",         "Informe seu nome completo:"),
    ("cpf",          "Informe seu CPF (apenas números):"),
    ("nasc",         "Data de nascimento (DD/MM/AAAA):"),
    ("especialidade","Qual especialidade você procura?"),
    ("endereco",     "Endereço (rua, nº, bairro, CEP, cidade/UF):"),
]
EXAMES_FIELDS = [
    ("forma",    "Convênio ou Particular?"),
    ("nome",     "Informe seu nome completo:"),
    ("cpf",      "Informe seu CPF (apenas números):"),
    ("nasc",     "Data de nascimento (DD/MM/AAAA):"),
    ("exame",    "Qual exame você procura?"),
    ("endereco", "Endereço (rua, nº, bairro, CEP, cidade/UF):"),
]
FECHAMENTO = {
    "consulta":  "✅ Obrigado! Um atendente irá entrar em contato com você para confirmar valores e agendar a data da consulta.",
    "exames":    "✅ Perfeito! Um atendente vai falar com você para agendar o exame.",
    "retorno":   "🧑‍⚕️ Um atendente vai entrar em contato com você para orientar seu retorno.",
    "resultado": "🧑‍⚕️ Um atendente vai entrar em contato com você sobre seu resultado.",
    "pesquisa":  "🙏 Obrigado! Isso ajuda nossa clínica. Um atendente poderá entrar em contato, se for necessário.",
}

def _prompt_basico(key):
    return {
        "nome":"Informe seu nome completo:",
        "cpf":"Informe seu CPF (apenas números):",
        "nasc":"Data de nascimento (DD/MM/AAAA):",
        "endereco":"Endereço (rua, nº, bairro, CEP, cidade/UF):",
    }.get(key, "Informe o dado solicitado:")

def _validate(key, value) -> Optional[str]:
    v = (value or "").strip()
    if key == "cpf":
        if len(_cpf_clean(v)) != 11:
            return "CPF inválido. Envie apenas números (11 dígitos)."
    if key == "nasc":
        if not _date_ok(v):
            return "Data inválida. Use o formato DD/MM/AAAA."
    if key in {"forma","nome","especialidade","exame","endereco"} and not v:
        return "Este campo é obrigatório."
    return None

def _normalize(key, value) -> str:
    v = (value or "").strip()
    if key == "cpf":
        return _cpf_clean(v)
    if key == "forma":
        low = v.lower()
        if "conv" in low:  return "Convênio"
        if "part" in low:  return "Particular"
    return v

# ====== Entrada principal ======
def responder_evento_mensagem(entry: dict) -> None:
    ss = _gspread()  # conecta (cria abas se faltar)
    value    = (entry.get("changes") or [{}])[0].get("value", {})
    messages = value.get("messages", [])
    contacts = value.get("contacts", [])
    if not messages or not contacts: return

    msg     = messages[0]
    wa_to   = contacts[0].get("wa_id") or msg.get("from")  # fallback
    mtype   = msg.get("type")

    # Início / Menu:
    if mtype == "text":
        body = (msg.get("text", {}).get("body") or "").strip()
        low  = body.lower()

        if low in {"oi","ola","olá","menu","iniciar","começar","start","+ opções","+ opcoes","+ opcoes","inicio","início"}:
            SESS[wa_to] = {"route":"root", "stage":"", "data":{}}
            _send_buttons(wa_to, WELCOME, BTN_ROOT)
            return

        # Se estiver em coleta
        ses = SESS.get(wa_to)
        if ses and ses.get("route") in {"consulta","exames","retorno","resultado","pesquisa"}:
            _continue_form(ss, wa_to, ses, body)
            return

        # Atalhos simples: se usuário digitou "consulta"/"exame" ao invés do botão
        if "consulta" in low:
            SESS[wa_to] = {"route":"consulta", "stage":"forma", "data":{"tipo":"consulta"}}
            _send_text(wa_to, CONSULTA_FIELDS[0][1]); return
        if "exame" in low:
            SESS[wa_to] = {"route":"exames", "stage":"forma", "data":{"tipo":"exames"}}
            _send_text(wa_to, EXAMES_FIELDS[0][1]); return

        # Sem contexto → mostra menu
        _send_buttons(wa_to, WELCOME, BTN_ROOT)
        return

    if mtype == "interactive":
        inter = msg.get("interactive", {})
        itype = inter.get("type")
        if itype == "button_reply":
            bid = (inter.get("button_reply", {}) or {}).get("id") or (inter.get("button_reply", {}) or {}).get("title")
        elif itype == "list_reply":
            bid = (inter.get("list_reply", {}) or {}).get("id") or (inter.get("list_reply", {}) or {}).get("title")
        else:
            bid = None

        if not bid:
            _send_buttons(wa_to, WELCOME, BTN_ROOT); return

        # Botões de primeiro nível
        if bid in {"op_consulta","Consulta"}:
            SESS[wa_to] = {"route":"consulta", "stage":"forma", "data":{"tipo":"consulta"}}
            _send_text(wa_to, CONSULTA_FIELDS[0][1]); return

        if bid in {"op_exames","Exames"}:
            SESS[wa_to] = {"route":"exames", "stage":"forma", "data":{"tipo":"exames"}}
            _send_text(wa_to, EXAMES_FIELDS[0][1]); return

        if bid in {"op_mais","+ Opções","+ Opcoes"}:
            SESS[wa_to] = {"route":"mais", "stage":"", "data":{}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS); return

        if bid in {"op_retorno","Retorno de consulta"}:
            SESS[wa_to] = {"route":"retorno", "stage":"cpf", "data":{"tipo":"retorno"}}
            _send_text(wa_to, "Informe seu CPF (apenas números):"); return

        if bid in {"op_resultado","Resultado de exames"}:
            SESS[wa_to] = {"route":"resultado", "stage":"cpf", "data":{"tipo":"resultado"}}
            _send_text(wa_to, "Informe seu CPF (apenas números):"); return

        if bid in {"op_pesquisa","Pesquisa"}:
            ses = {"route":"pesquisa", "stage":"", "data":{}}
            SESS[wa_to] = ses
            _start_pesquisa(wa_to, ses); return

        # fallback
        _send_buttons(wa_to, WELCOME, BTN_ROOT)
        return

def _start_pesquisa(wa_to, ses):
    needed = ["nome","cpf","nasc","endereco"]
    missing = [k for k in needed if not ses["data"].get(k)]
    if missing:
        ses["stage"] = missing[0]
        _send_text(wa_to, _prompt_basico(missing[0]))
    else:
        ses["stage"] = "especialidade"
        _send_text(wa_to, "Qual especialidade você procura?")

def _continue_form(ss, wa_to, ses, user_text):
    route = ses["route"]
    stage = ses.get("stage","")
    data  = ses["data"]

    # Retorno/Resultado: primeiro CPF → tenta localizar
    if route in {"retorno","resultado"} and stage == "cpf":
        cpf = _cpf_clean(user_text)
        if len(cpf) != 11:
            _send_text(wa_to, "CPF inválido. Envie apenas números (11 dígitos)."); return
        data["cpf"] = cpf
        pac = _find_paciente(ss, cpf)
        if pac:
            payload = {
                "tipo": route, "forma": pac.get("forma",""), "cpf": cpf, "nome": pac.get("nome",""),
                "nasc": pac.get("nasc",""), "especialidade":"", "exame":"", "endereco": pac.get("endereco","")
            }
            _add_solicitacao(ss, payload)
            _send_text(wa_to, "Localizamos seu cadastro.")
            _send_text(wa_to, FECHAMENTO["retorno" if route=="retorno" else "resultado"])
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return
        else:
            ses["stage"] = "forma"
            _send_text(wa_to, "Não encontramos seu cadastro. Informe: Convênio ou Particular?")
            return

    # Campos por fluxo
    fields = (
        CONSULTA_FIELDS if route == "consulta" else
        EXAMES_FIELDS if route == "exames" else
        [("forma","Convênio ou Particular?"),("nome","Informe seu nome completo:"),
         ("cpf","Informe seu CPF (apenas números):"),("nasc","Data de nascimento (DD/MM/AAAA):"),
         ("especialidade" if route=="retorno" else "exame", "Qual especialidade/exame?"),
         ("endereco","Endereço (rua, nº, bairro, CEP, cidade/UF):")]
        if route in {"retorno","resultado"} else None
    )

    # Pesquisa: coleta básicos → especialidade/exame → grava
    if route == "pesquisa":
        needed = ["nome","cpf","nasc","endereco","especialidade","exame"]
        if stage:
            err = _validate(stage, user_text)
            if err: _send_text(wa_to, err); return
            data[stage] = _normalize(stage, user_text)
        for k in needed:
            if not data.get(k):
                ses["stage"] = k
                _send_text(wa_to, _prompt_basico(k) if k in {"nome","cpf","nasc","endereco"} else
                                   ("Qual especialidade você procura?" if k=="especialidade" else "Qual exame você procura?"))
                return
        _add_pesquisa(ss, data)
        _send_text(wa_to, FECHAMENTO["pesquisa"])
        SESS[wa_to] = {"route":"root","stage":"","data":{}}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    # Consulta/Exames/Retorno completo: valida e avança
    if stage:
        err = _validate(stage, user_text)
        if err: _send_text(wa_to, err); return
        data[stage] = _normalize(stage, user_text)

    pending = [(k,q) for (k,q) in fields if not data.get(k)]
    if pending:
        ses["stage"] = pending[0][0]
        _send_text(wa_to, pending[0][1])
        return

    # Finaliza
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)
    _send_text(wa_to, FECHAMENTO[route] if route in FECHAMENTO else "Solicitação registrada.")
    SESS[wa_to] = {"route":"root","stage":"","data":{}}
    _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
