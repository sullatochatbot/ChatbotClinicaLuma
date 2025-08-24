# responder_clinica.py — Fluxo final Clínica Luma (com convênio e particular corrigidos)
# ==============================================================================

# ====== Imports & Tipagem =====================================================
import os, re, json, requests
from datetime import datetime
from typing import Dict, Any, Optional

# ====== Variáveis de Ambiente (WhatsApp / Sheets / Links) =====================
WA_ACCESS_TOKEN         = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
CLINICA_SHEET_ID        = os.getenv("CLINICA_SHEET_ID", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Luma").strip()
LINK_SITE      = os.getenv("LINK_SITE", "").strip()
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "").strip()

GRAPH_URL = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages" if WA_PHONE_NUMBER_ID else ""
HEADERS   = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}

# ====== Google Sheets (gspread) ===============================================
import gspread
from google.oauth2.service_account import Credentials

def _gspread():
    if not CLINICA_SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
        raise RuntimeError("Config Sheets ausente. Defina CLINICA_SHEET_ID e GOOGLE_CREDENTIALS_JSON no ambiente.")
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(CLINICA_SHEET_ID)
    _ensure_ws(ss, "Pacientes",    ["cpf","nome","nasc","endereco","forma","convenio","tipo","created_at"])
    _ensure_ws(ss, "Solicitacoes", ["timestamp","tipo","forma","convenio","cpf","nome","nasc","especialidade","exame","endereco"])
    _ensure_ws(ss, "Pesquisa",     ["timestamp","cpf","nome","nasc","endereco","especialidade","exame"])
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

# ====== Utilitários ===========================================================
# ====== Ajuste de fuso horário ===============================================
from datetime import datetime
import pytz

def _hora_sp():
    tz = pytz.timezone("America/Sao_Paulo")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def _first_name(fullname: str) -> str:
    n = (fullname or "").strip()
    return n.split()[0] if n else ""

def _send_text(to, text):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA TEXT]", to, text); return
    payload = {"messaging_product":"whatsapp","to":to,"type":"text",
               "text":{"preview_url":False,"body":text[:4096]}}
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

# ====== UI (Boas-vindas e Botões) ============================================
WELCOME_GENERIC = f"Bem-vindo à {NOME_EMPRESA}! Escolha uma opção abaixo para começar."
def _welcome_named(name: str) -> str:
    fn = _first_name(name)
    if fn:
        return f"Bem-vindo(a), {fn}! Este é o atendimento virtual da {NOME_EMPRESA}. Escolha uma opção:"
    return WELCOME_GENERIC

BTN_ROOT = [
    {"id":"op_consulta","title":"Consulta"},
    {"id":"op_exames","title":"Exames"},
    {"id":"op_mais","title":"+ Opções"},
]

# “+ Opções” — nível 1
BTN_MAIS_1 = [
    {"id":"op_endereco","title":"Endereço"},
    {"id":"op_contato","title":"Contato"},
    {"id":"op_mais2","title":"+ Opções"},
]
# “+ Opções” — nível 2 (o que procura)
BTN_MAIS_2 = [
    {"id":"op_especialidade","title":"Especialidade"},
    {"id":"op_exames_atalho","title":"Exames"},
    {"id":"op_voltar_root","title":"Voltar"},
]

# Botões para forma
BTN_FORMA = [
    {"id":"forma_convenio","title":"Convênio"},
    {"id":"forma_particular","title":"Particular"},
]

# ====== Validadores e Normalizadores =========================================
_RE_CPF   = re.compile(r"\D")
_RE_DATE  = re.compile(r"^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/\d{4}$")
def _cpf_clean(s): return _RE_CPF.sub("", s or "")
def _date_ok(s): return bool(_RE_DATE.match(s or ""))

def _validate(key, value, *, data=None) -> Optional[str]:
    v = (value or "").strip()
    if key == "cpf":
        if len(_cpf_clean(v)) != 11:
            return "CPF inválido. Envie apenas números (11 dígitos)."
    if key == "nasc":
        if not _date_ok(v):
            return "Data inválida. Use o formato DD/MM/AAAA."
    if key == "convenio":
        # só obrigatório quando forma == Convênio
        if (data or {}).get("forma") == "Convênio" and not v:
            return "Informe o nome do seu convênio."
        # se for Particular, não exigimos
        return None
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

def _ask_forma(wa_to):
    _send_buttons(wa_to, "Convênio ou Particular?", BTN_FORMA)

# ====== Persistência (Sheets) =================================================
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
        data.get("forma",""), data.get("convenio",""), data.get("tipo",""), _hora_sp()
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_solicitacao(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Solicitacoes")
    row = [
        _hora_sp(), data.get("tipo",""), data.get("forma",""), data.get("convenio",""),
        data.get("cpf",""), data.get("nome",""), data.get("nasc",""),
        data.get("especialidade",""), data.get("exame",""), data.get("endereco","")
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_pesquisa(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Pesquisa")
    row = [_hora_sp(), data.get("cpf",""), data.get("nome",""), data.get("nasc",""),
           data.get("endereco",""), data.get("especialidade",""), data.get("exame","")]
    ws.append_row(row, value_input_option="USER_ENTERED")

# ====== Sessão em Memória =====================================================
SESS: Dict[str, Dict[str, Any]] = {}  # wa_id -> {"route": str, "stage": str, "data": dict}

# ====== Campos (dinâmicos conforme forma) =====================================
def _fields_for(route: str, data: Dict[str,Any]):
    """Retorna a lista de (campo, pergunta) dinâmica para cada fluxo."""
    def _comuns_consulta():
        campos = [("forma","Convênio ou Particular?")]
        if data.get("forma") == "Convênio":
            campos.append(("convenio","Qual é o nome do seu convênio?"))
        campos += [
            ("nome","Informe seu nome completo:"),
            ("cpf","Informe seu CPF (apenas números):"),
            ("nasc","Data de nascimento (DD/MM/AAAA):"),
            ("especialidade","Qual especialidade você procura?"),
            ("endereco","Endereço (rua, nº, bairro, CEP, cidade/UF):"),
        ]
        return campos

    def _comuns_exames():
        campos = [("forma","Convênio ou Particular?")]
        if data.get("forma") == "Convênio":
            campos.append(("convenio","Qual é o nome do seu convênio?"))
        campos += [
            ("nome","Informe seu nome completo:"),
            ("cpf","Informe seu CPF (apenas números):"),
            ("nasc","Data de nascimento (DD/MM/AAAA):"),
            ("exame","Qual exame você procura?"),
            ("endereco","Endereço (rua, nº, bairro, CEP, cidade/UF):"),
        ]
        return campos

    if route == "consulta":
        return _comuns_consulta()
    if route == "exames":
        return _comuns_exames()
    if route in {"retorno","resultado"}:
        campos = [("forma","Convênio ou Particular?")]
        if data.get("forma") == "Convênio":
            campos.append(("convenio","Qual é o nome do seu convênio?"))
        campos += [
            ("nome","Informe seu nome completo:"),
            ("cpf","Informe seu CPF (apenas números):"),
            ("nasc","Data de nascimento (DD/MM/AAAA):"),
            ("especialidade" if route=="retorno" else "exame","Qual especialidade/exame?"),
            ("endereco","Endereço (rua, nº, bairro, CEP, cidade/UF):"),
        ]
        return campos
    return None

# ====== Fechamentos ===========================================================
FECHAMENTO = {
    "consulta":  "✅ Obrigado! Um atendente irá entrar em contato com você para confirmação e agendar a data da consulta.",
    "exames":    "✅ Perfeito! Um atendente vai falar com você para agendar o exame.",
    "retorno":   "🧑‍⚕️ Um atendente vai entrar em contato com você para orientar seu retorno.",
    "resultado": "🧑‍⚕️ Um atendente vai entrar em contato com você sobre seu resultado.",
    "pesquisa":  "🙏 Obrigado! Isso ajuda nossa clínica. Um atendente poderá entrar em contato, se for necessário.",
}

# ====== Prompts Básicos =======================================================
def _prompt_basico(key):
    return {
        "nome":"Informe seu nome completo:",
        "cpf":"Informe seu CPF (apenas números):",
        "nasc":"Data de nascimento (DD/MM/AAAA):",
        "endereco":"Endereço (rua, nº, bairro, CEP, cidade/UF):",
        "convenio":"Qual é o nome do seu convênio?",
    }.get(key, "Informe o dado solicitado:")

# ====== Handler principal (Webhook) ===========================================
def responder_evento_mensagem(entry: dict) -> None:
    ss = _gspread()
    value    = (entry.get("changes") or [{}])[0].get("value", {})
    messages = value.get("messages", [])
    contacts = value.get("contacts", [])
    if not messages or not contacts: return

    msg     = messages[0]
    wa_to   = contacts[0].get("wa_id") or msg.get("from")
    profile_name = (contacts[0].get("profile") or {}).get("name") or ""

    mtype   = msg.get("type")

    # -- BOTÕES / INTERACTIVE --------------------------------------------------
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
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

        # Raiz
        if bid in {"op_consulta","Consulta"}:
            SESS[wa_to] = {"route":"consulta", "stage":"forma", "data":{"tipo":"consulta"}}
            _ask_forma(wa_to); return

        if bid in {"op_exames","Exames"}:
            SESS[wa_to] = {"route":"exames", "stage":"forma", "data":{"tipo":"exames"}}
            _ask_forma(wa_to); return

        if bid in {"op_mais","+ Opções","+ Opcoes"}:
            SESS[wa_to] = {"route":"mais", "stage":"", "data":{}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS_1); return

        # “+ Opções” nível 1
        if bid == "op_endereco":
            txt = "Nosso endereço/contato:\n"
            if LINK_SITE:      txt += f"• Site: {LINK_SITE}\n"
            if LINK_INSTAGRAM: txt += f"• Instagram: {LINK_INSTAGRAM}\n"
            _send_text(wa_to, txt.strip() or "Em breve informaremos endereço/contato.")
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return

        if bid == "op_contato":
            txt = "Fale conosco:\n"
            if LINK_SITE:      txt += f"• Site: {LINK_SITE}\n"
            if LINK_INSTAGRAM: txt += f"• Instagram: {LINK_INSTAGRAM}\n"
            _send_text(wa_to, txt.strip() or "Em breve disponibilizaremos os canais de contato.")
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return

        if bid == "op_mais2":
            _send_buttons(wa_to, "O que você procura?", BTN_MAIS_2); return

        if bid == "op_voltar_root":
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

        # Submenu — atalhos de pesquisa
        if bid == "op_especialidade":
            ses = {"route":"pesquisa", "stage":"especialidade", "data":{}}
            SESS[wa_to] = ses
            _send_text(wa_to, "Qual especialidade você procura?"); return

        if bid == "op_exames_atalho":
            ses = {"route":"pesquisa", "stage":"exame", "data":{}}
            SESS[wa_to] = ses
            _send_text(wa_to, "Qual exame você procura?"); return

        # Botões de forma
        if bid in {"forma_convenio","forma_particular"}:
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            # Se o usuário clicou "Convênio/Particular" fora de um fluxo (clique tardio),
            # iniciamos automaticamente o fluxo de CONSULTA.
            if ses.get("route") not in {"consulta","exames","retorno","resultado","pesquisa"}:
                ses = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            ses["data"]["forma"] = "Convênio" if bid == "forma_convenio" else "Particular"
            SESS[wa_to] = ses
            _after_forma_prompt_next(wa_to, ses); return

        # fallback
        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return

    # -- TEXTO -----------------------------------------------------------------
    if mtype == "text":
        body = (msg.get("text", {}).get("body") or "").strip()
        low  = body.lower()

        # Em coleta? Continua
        ses = SESS.get(wa_to)
        if ses and ses.get("route") in {"consulta","exames","retorno","resultado","pesquisa"} and ses.get("stage"):
            _continue_form(ss, wa_to, ses, body)
            return

        # Atalhos digitados
        if "consulta" in low:
            SESS[wa_to] = {"route":"consulta", "stage":"forma", "data":{"tipo":"consulta"}}
            _ask_forma(wa_to); return
        if "exame" in low:
            SESS[wa_to] = {"route":"exames", "stage":"forma", "data":{"tipo":"exames"}}
            _ask_forma(wa_to); return
        if low in {"+ opções","+ opcoes","+opcoes","+opções","opções","opcoes"}:
            SESS[wa_to] = {"route":"mais", "stage":"", "data":{}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS_1); return

        # Qualquer texto → boas-vindas com nome
        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return

# ====== Auxiliares de Fluxo (após escolher forma; continuar coleta) ===========
def _after_forma_prompt_next(wa_to, ses):
    """Após selecionar Convênio/Particular, decide o próximo campo (dinâmico)."""
    route = ses.get("route")
    data  = ses.get("data", {})
    fields = _fields_for(route, data) or []
    pending = [(k,q) for (k,q) in fields if not data.get(k)]
    if pending:
        next_key, question = pending[0]
        if next_key == "forma":
            _ask_forma(wa_to)
        else:
            SESS[wa_to]["stage"] = next_key
            _send_text(wa_to, question)
    else:
        SESS[wa_to]["stage"] = None  # força finalizar no próximo handler

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
                "tipo": route, "forma": pac.get("forma",""), "convenio": pac.get("convenio",""),
                "cpf": cpf, "nome": pac.get("nome",""), "nasc": pac.get("nasc",""),
                "especialidade":"", "exame":"", "endereco": pac.get("endereco","")
            }
            _add_solicitacao(ss, payload)
            _send_text(wa_to, "Localizamos seu cadastro.")
            _send_text(wa_to, FECHAMENTO["retorno" if route=="retorno" else "resultado"])
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return
        else:
            ses["stage"] = "forma"
            _ask_forma(wa_to)
            return

    # Campos para o fluxo atual (dinâmicos conforme forma)
    fields = _fields_for(route, data)

    # Pesquisa: coleta básicos → especialidade/exame → grava
    if route == "pesquisa":
        needed = ["nome","cpf","nasc","endereco","especialidade","exame"]
        if stage:
            err = _validate(stage, user_text, data=data)
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

    # Consulta/Exames/Retorno: validar e avançar
    if stage:
        # 'forma' é via botão — se digitou, normalizamos e seguimos
        if stage == "forma" and user_text:
            data["forma"] = _normalize("forma", user_text)
        else:
            err = _validate(stage, user_text, data=data)
            if err: _send_text(wa_to, err); return
            data[stage] = _normalize(stage, user_text)

    pending = [(k,q) for (k,q) in (fields or []) if not data.get(k)]
    if pending:
        next_key, question = pending[0]
        ses["stage"] = next_key
        if next_key == "forma":
            _ask_forma(wa_to)
        else:
            _send_text(wa_to, question)
        return

    # Finaliza (upsert paciente + solicitações + fechamento)
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)

    # fechamento do fluxo
    _send_text(wa_to, FECHAMENTO.get(route, "Solicitação registrada."))
    # reseta a sessão, mas NÃO reenvia menu
    SESS[wa_to] = {"route":"root","stage":"","data":{}}
    return

