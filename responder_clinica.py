# responder_clinica.py — Fluxo final Clínica Luma
# ==============================================================================

# ====== Imports & Tipagem =====================================================
import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional

# ====== Variáveis de Ambiente (WhatsApp / Sheets / Links) =====================
WA_ACCESS_TOKEN         = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
CLINICA_SHEET_ID        = os.getenv("CLINICA_SHEET_ID", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Luma").strip()
LINK_SITE      = os.getenv("LINK_SITE", "https://www.lumaclinicadafamilia.com.br").strip()
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "https://www.instagram.com/luma_clinicamedica").strip()

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

    # >>> HEADERS (agora com cep, numero, complemento)
    _ensure_ws(ss, "Pacientes", [
        "cpf","nome","nasc","endereco","cep","numero","complemento",
        "forma","convenio","tipo","created_at"
    ])
    _ensure_ws(ss, "Solicitacoes", [
        "timestamp","tipo","forma","convenio","cpf","nome","nasc",
        "especialidade","exame","endereco","cep","numero","complemento"
    ])
    _ensure_ws(ss, "Pesquisa", [
        "timestamp","cpf","nome","nasc","endereco","cep","numero","complemento",
        "especialidade","exame"
    ])
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

# ====== Ajuste de fuso horário ===============================================
def _hora_sp():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")

# ====== CEP + ViaCEP ==========================================================
_RE_CEP = re.compile(r"^\d{8}$")

def _cep_ok(s: str) -> bool:
    s = re.sub(r"\D", "", s or "")
    return bool(_RE_CEP.match(s))

def _via_cep(cep: str) -> Optional[dict]:
    cep = re.sub(r"\D", "", cep or "")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=10)
        if r.status_code >= 300:
            return None
        j = r.json()
        if j.get("erro"):
            return None
        return j
    except Exception:
        return None

def _montar_endereco_via_cep(cep: str, numero: str, complemento: str = "") -> Optional[str]:
    data = _via_cep(cep)
    if not data:
        return None
    log = (data.get("logradouro") or "").strip()
    bai = (data.get("bairro") or "").strip()
    cid = (data.get("localidade") or "").strip()
    uf  = (data.get("uf") or "").strip()
    cep_num = re.sub(r"\D", "", cep or "")
    cep_fmt = f"{cep_num[:5]}-{cep_num[5:]}" if len(cep_num) == 8 else cep_num
    partes = []
    if log: partes.append(log)
    if numero: partes.append(f", {numero}")
    if complemento: partes.append(f" - {complemento}")
    if bai: partes.append(f" - {bai}")
    if cid or uf: partes.append(f" - {cid}/{uf}".replace("//","/"))
    if cep_fmt: partes.append(f" – CEP {cep_fmt}")
    return "".join(partes).strip()

# ====== Utilitários ===========================================================
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

BTN_MAIS_1 = [
    {"id":"op_endereco","title":"Endereço"},
    {"id":"op_contato","title":"Contato"},
    {"id":"op_mais2","title":"+ Opções"},
]

BTN_MAIS_2 = [
    {"id":"op_especialidade","title":"Especialidade"},
    {"id":"op_exames_atalho","title":"Exames"},
    {"id":"op_voltar_root","title":"Voltar"},
]

BTN_FORMA = [
    {"id":"forma_convenio","title":"Convênio"},
    {"id":"forma_particular","title":"Particular"},
]

# >>> BOTÕES: complemento (sim/não)
BTN_COMPLEMENTO = [
    {"id":"compl_sim","title":"Sim"},
    {"id":"compl_nao","title":"Não"},
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
        if (data or {}).get("forma") == "Convênio" and not v:
            return "Informe o nome do seu convênio."
        return None
    if key == "cep":
        if not _cep_ok(v):
            return "CEP inválido. Envie 8 dígitos (ex: 03878000)."
    if key == "numero":
        if not v:
            return "Informe o número."
    if key in {"forma","nome","especialidade","exame"} and not v:
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
    col = ws.col_values(1)
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
    if cpf in col: return
    row = [
        data.get("cpf",""),
        data.get("nome",""),
        data.get("nasc",""),
        data.get("endereco",""),
        data.get("cep",""),
        data.get("numero",""),
        data.get("complemento",""),
        data.get("forma",""),
        data.get("convenio",""),
        data.get("tipo",""),
        _hora_sp(),
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_solicitacao(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Solicitacoes")
    row = [
        _hora_sp(),
        data.get("tipo",""),
        data.get("forma",""),
        data.get("convenio",""),
        data.get("cpf",""),
        data.get("nome",""),
        data.get("nasc",""),
        data.get("especialidade",""),
        data.get("exame",""),
        data.get("endereco",""),
        data.get("cep",""),
        data.get("numero",""),
        data.get("complemento",""),
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

def _add_pesquisa(ss, data: Dict[str,Any]):
    ws = ss.worksheet("Pesquisa")
    row = [
        _hora_sp(),
        data.get("cpf",""),
        data.get("nome",""),
        data.get("nasc",""),
        data.get("endereco",""),
        data.get("cep",""),
        data.get("numero",""),
        data.get("complemento",""),
        data.get("especialidade",""),
        data.get("exame",""),
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

# ====== Sessão em Memória =====================================================
SESS: Dict[str, Dict[str, Any]] = {}

# ====== Campos (dinâmicos conforme forma) =====================================
def _comuns_consulta(data):
    campos = [("forma","Convênio ou Particular?")]
    if data.get("forma") == "Convênio":
        campos.append(("convenio","Qual é o nome do seu convênio?"))
    campos += [
        ("nome","Informe seu nome completo:"),
        ("cpf","Informe seu CPF (apenas números):"),
        ("nasc","Data de nascimento (DD/MM/AAAA):"),
        ("especialidade","Qual especialidade você procura?"),
        ("cep","Informe seu CEP (apenas números, ex: 03878000):"),
        ("numero","Informe o número:"),
    ]
    return campos

def _comuns_exames(data):
    campos = [("forma","Convênio ou Particular?")]
    if data.get("forma") == "Convênio":
        campos.append(("convenio","Qual é o nome do seu convênio?"))
    campos += [
        ("nome","Informe seu nome completo:"),
        ("cpf","Informe seu CPF (apenas números):"),
        ("nasc","Data de nascimento (DD/MM/AAAA):"),
        ("exame","Qual exame você procura?"),
        ("cep","Informe seu CEP (apenas números, ex: 03878000):"),
        ("numero","Informe o número:"),
    ]
    return campos

def _fields_for(route: str, data: Dict[str,Any]):
    if route == "consulta": return _comuns_consulta(data)
    if route == "exames":   return _comuns_exames(data)
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
        "cep":"Informe seu CEP (apenas números, ex: 03878000):",
        "numero":"Informe o número:",
        "complemento":"Complemento (apto, bloco, sala):",
    }.get(key, "Informe o dado solicitado:")
# ====== Handler principal =====================================================
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
            txt = (
                "📍 *Endereço*\n"
                "Rua Utrecht, 129 – Vila Rio Branco – CEP 03878-000 – São Paulo/SP\n\n"
                f"🌐 *Site*: {LINK_SITE}\n"
                f"📷 *Instagram*: {LINK_INSTAGRAM}\n"
                "📘 *Facebook*: Clinica Luma\n"
                "☎️ *Telefone*: (11) 2043-9937\n"
                "💬 *WhatsApp*: https://wa.me/5511968501810\n"
            )
            _send_text(wa_to, txt)
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
            if ses.get("route") not in {"consulta","exames","retorno","resultado","pesquisa"}:
                ses = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            ses["data"]["forma"] = "Convênio" if bid == "forma_convenio" else "Particular"
            SESS[wa_to] = ses
            _after_forma_prompt_next(wa_to, ses); return

        # >>> HANDLER: complemento sim/não
        if bid == "compl_sim":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return

        if bid == "compl_nao":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["data"]["complemento"] = ""  # sem complemento
            SESS[wa_to] = ses
            _after_forma_prompt_next(wa_to, ses)
            return

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
            _continue_form(_gspread(), wa_to, ses, body)
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

# ====== Auxiliares de Fluxo ===================================================
def _after_forma_prompt_next(wa_to, ses):
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
                "especialidade":"", "exame":"", "endereco": pac.get("endereco",""),
                "cep": pac.get("cep",""), "numero": pac.get("numero",""), "complemento": pac.get("complemento","")
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

    # Consulta/Exames: validar e avançar
    if stage:
        if stage == "forma" and user_text:
            data["forma"] = _normalize("forma", user_text)
        else:
            err = _validate(stage, user_text, data=data)
            if err: _send_text(wa_to, err); return
            data[stage] = _normalize(stage, user_text)

    # Após número → pergunta “Possui complemento?” com botões
    if route in {"consulta","exames"} and stage == "numero":
        SESS[wa_to]["stage"] = "complemento_pending"
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        return

    # Se o usuário digitou o complemento (quando escolheu "Sim"), segue normal

    # >>> Montagem do ENDEREÇO (CEP→ViaCEP)
    if route in {"consulta","exames"}:
        if data.get("complemento","").strip().lower() in {"sem","s/","s"}:
            data["complemento"] = ""
        if data.get("cep") and data.get("numero") and ("complemento" in data) and not data.get("endereco"):
            endereco_montado = _montar_endereco_via_cep(data["cep"], data["numero"], data.get("complemento",""))
            if endereco_montado:
                data["endereco"] = endereco_montado
            else:
                _send_text(wa_to, "Não consegui localizar o CEP. Confirme o CEP (8 dígitos) ou envie o endereço completo.")
                ses["stage"] = "cep"
                return

    pending = [(k,q) for (k,q) in (fields or []) if not data.get(k)]
    if pending:
        next_key, question = pending[0]
        ses["stage"] = next_key
        if next_key == "forma":
            _ask_forma(wa_to)
        else:
            _send_text(wa_to, question)
        return

    # Finaliza
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)
    _send_text(wa_to, FECHAMENTO.get(route, "Solicitação registrada."))
    SESS[wa_to] = {"route":"root","stage":"","data":{}}
