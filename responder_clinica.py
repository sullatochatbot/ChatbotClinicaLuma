# responder_clinica.py — Clínica Luma
# ==============================================================================

# ===== PARTE 1 =================================================================
import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List

# ===== Variáveis de ambiente ==================================================
WA_ACCESS_TOKEN         = os.getenv("WA_ACCESS_TOKEN", "").strip() or os.getenv("ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "").strip() or os.getenv("PHONE_NUMBER_ID", "").strip()
CLINICA_SHEET_ID        = os.getenv("CLINICA_SHEET_ID", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Luma").strip()
LINK_SITE      = os.getenv("LINK_SITE", "https://www.lumaclinicadafamilia.com.br").strip()
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "https://www.instagram.com/luma_clinicamedica").strip()

GRAPH_URL = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages" if WA_PHONE_NUMBER_ID else ""
HEADERS   = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}

# ===== Google Sheets ==========================================================
import gspread
from google.oauth2.service_account import Credentials

def _gspread():
    if not GOOGLE_CREDENTIALS_JSON or not CLINICA_SHEET_ID:
        raise RuntimeError("Faltam GOOGLE_CREDENTIALS_JSON ou CLINICA_SHEET_ID no .env")

    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(CLINICA_SHEET_ID)

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
    _ensure_ws(ss, "Sugestoes", ["timestamp","categoria","texto","wa_id"])
    return ss

def _ensure_ws(ss, title, headers):
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows="1000", cols=str(len(headers)+2))
        ws.insert_row(headers, index=1)
    else:
        if ws.row_values(1) != headers:
            ws.resize(rows=max(ws.row_count, 1000), cols=len(headers))
            ws.update(f"A1:{chr(64+len(headers))}1", [headers])

# ===== Utilitários ============================================================
def _hora_sp():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")

_RE_CEP = re.compile(r"^\d{8}$")
def _cep_ok(s):
    return bool(_RE_CEP.match(re.sub(r"\D","",s or "")))

def _via_cep(cep):
    cep = re.sub(r"\D","",cep or "")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=10)
        if r.status_code >= 300: return None
        j = r.json()
        if j.get("erro"): return None
        return j
    except:
        return None

def _montar_endereco_via_cep(cep, numero, complemento=""):
    data = _via_cep(cep)
    if not data: return None
    log = (data.get("logradouro") or "")
    bai = (data.get("bairro") or "")
    cid = (data.get("localidade") or "")
    uf  = (data.get("uf") or "")
    cep_num = re.sub(r"\D","",cep or "")
    cep_fmt = f"{cep_num[:5]}-{cep_num[5:]}" if len(cep_num)==8 else cep_num
    comp = f" - {complemento.strip()}" if complemento else ""
    return f"{log}, {numero}{comp} - {bai} - {cid}/{uf} – CEP {cep_fmt}".strip()

def _send_text(to: str, text: str):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA TEXT]", to, text)
        return
    payload = {
        "messaging_product":"whatsapp","to":to,"type":"text",
        "text":{"preview_url":False,"body":text[:4096]}
    }
    requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

def _send_buttons(to: str, body: str, buttons: List[Dict[str,str]]):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA BTNS]", to, body, buttons)
        return
    payload = {
        "messaging_product":"whatsapp","to":to,"type":"interactive",
        "interactive":{
            "type":"button",
            "body":{"text":body[:1024]},
            "action":{"buttons":[{"type":"reply","reply":b} for b in buttons[:3]]}
        }
    }
    requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

# ===== Botões/UI ==============================================================
WELCOME_GENERIC = f"Bem-vindo à {NOME_EMPRESA}! Escolha uma opção abaixo para começar."

def _welcome_named(name):
    return (
        f"Bem-vindo(a), {name.split()[0]}! Este é o atendimento virtual da {NOME_EMPRESA}."
        if name else WELCOME_GENERIC
    )

# Menu 1
BTN_ROOT = [
    {"id": "op_consulta", "title": "Consulta"},
    {"id": "op_exames",   "title": "Exames"},
    {"id": "op_mais",     "title": "+ Opções"},
]

# Menu 2 (+ Opções do menu 1)
BTN_MAIS_2 = [
    {"id": "op_retorno",    "title": "Retorno de consultas"},
    {"id": "op_resultado",  "title": "Resultado de exames"},
    {"id": "op_mais3",      "title": "+ Opções"}
]

# Menu 3 (+ Opções do menu 2)
BTN_MAIS_3 = [
    {"id": "op_endereco",        "title": "Endereço"},
    {"id": "op_editar_endereco", "title": "Editar dados gerais"},
    {"id": "op_mais4",           "title": "+ Opções"}
]

# Menu 4 (+ Opções do menu 3)
BTN_MAIS_4 = [
    {"id": "op_sugestoes",   "title": "Sugestões"},
    {"id": "op_voltar_root", "title": "Voltar ao início"}
]

BTN_FORMA = [
    {"id": "forma_convenio",   "title": "Convênio"},
    {"id": "forma_particular", "title": "Particular"},
]

BTN_COMPLEMENTO = [
    {"id": "compl_sim", "title": "Sim"},
    {"id": "compl_nao", "title": "Não"},
]

BTN_CONFIRMA = [
    {"id": "confirmar", "title": "Confirmar"},
    {"id": "corrigir",  "title": "Corrigir"},
]

BTN_PACIENTE = [
    {"id": "pac_voce",  "title": "Eu mesmo(a)"},
    {"id": "pac_outro", "title": "Outro paciente"},
]

BTN_PAC_DOC = [
    {"id": "pacdoc_sim", "title": "Sim"},
    {"id": "pacdoc_nao", "title": "Não"},
]

MSG_SUGESTOES = (
    "💡 Ajude a Clínica Luma a melhorar! Diga quais *especialidades* ou *exames* "
    "você gostaria que tivéssemos."
)

# ===== Validadores e normalização ============================================
_RE_CPF  = re.compile(r"\D")
_RE_DATE = re.compile(r"^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/\d{4}$")

def _cpf_clean(s): return _RE_CPF.sub("", s or "")
def _date_ok(s: str) -> bool:
    try:
        raw = (s or "").strip()
        # aceita com / ou - ou só dígitos
        dig = re.sub(r"\D", "", raw)
        if len(dig) == 8:
            datetime.strptime(f"{dig[:2]}/{dig[2:4]}/{dig[4:]}", "%d/%m/%Y")
            return True
        datetime.strptime(raw.replace("-", "/"), "%d/%m/%Y")
        return True
    except Exception:
        return False

def _validate(key, v, *, data=None):
    v = (v or "").strip()
    if key=="cpf" and len(_cpf_clean(v))!=11:         return "CPF inválido."
    if key=="nasc" and not _date_ok(v):               return "Data inválida. Use o formato dd/mm/aaaa."
    if key=="convenio" and (data or {}).get("forma")=="Convênio" and not v: return "Informe o convênio."
    if key=="cep" and not _cep_ok(v):                 return "CEP inválido (8 dígitos)."
    if key=="numero" and not v:                       return "Informe o número."
    if key in {"forma","nome","especialidade","exame"} and not v: return "Obrigatório."
    return None

def _normalize(key, v):
    v = (v or "").strip()
    if key=="cpf":
        return _cpf_clean(v)
    if key=="forma":
        l = v.lower()
        if "conv" in l: return "Convênio"
        if "part" in l: return "Particular"
    if key == "nasc":
        s = re.sub(r"\D", "", v)
        if len(s) == 8:
            return f"{s[:2]}/{s[2:4]}/{s[4:]}"
        return (v or "").replace("-", "/")
    if key == "cep":
        return re.sub(r"\D", "", v)[:8]
    return v

def _ask_forma(to):
    _send_buttons(to, "Convênio ou Particular?", BTN_FORMA)

# ===== YES/NO (texto) =========================================================
def _is_yes(txt: str) -> bool:
    return (txt or "").strip().lower() in {"sim","s","yes","y"}

def _is_no(txt: str) -> bool:
    return (txt or "").strip().lower() in {"nao","não","n","no"}

# ===== Persistência ===========================================================
def _upsert_paciente(ss, d):
    ws  = ss.worksheet("Pacientes")
    cpf = d.get("cpf")
    if not cpf: return
    col = ws.col_values(1)
    if cpf in col: return
    ws.append_row([
        d.get("cpf",""), d.get("nome",""), d.get("nasc",""), d.get("endereco",""),
        d.get("cep",""), d.get("numero",""), d.get("complemento",""),
        d.get("forma",""), d.get("convenio",""), d.get("tipo",""), _hora_sp()
    ], value_input_option="USER_ENTERED")

def _add_solicitacao(ss, d):
    ws = ss.worksheet("Solicitacoes")
    ws.append_row([
        _hora_sp(), d.get("tipo",""), d.get("forma",""), d.get("convenio",""),
        d.get("cpf",""), d.get("nome",""), d.get("nasc",""),
        d.get("especialidade",""), d.get("exame",""),
        d.get("endereco",""), d.get("cep",""), d.get("numero",""),
        d.get("complemento","")
    ], value_input_option="USER_ENTERED")

def _add_pesquisa(ss, d):
    ws = ss.worksheet("Pesquisa")
    ws.append_row([
        _hora_sp(), d.get("cpf",""), d.get("nome",""), d.get("nasc",""), d.get("endereco",""),
        d.get("cep",""), d.get("numero",""), d.get("complemento",""),
        d.get("especialidade",""), d.get("exame","")
    ], value_input_option="USER_ENTERED")

def _add_sugestao(ss, categoria: str, texto: str, wa_id: str):
    ws = ss.worksheet("Sugestoes")
    ws.append_row([_hora_sp(), categoria, texto, wa_id], value_input_option="USER_ENTERED")

# ===== Sessão ================================================================
SESS: Dict[str, Dict[str, Any]] = {}

# ===== PARTE 2 =================================================================
# Campos dinâmicos + helper de pergunta
def _comuns_consulta(d):
    # Especialidade logo após forma/convênio
    campos = [("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio":
        campos.append(("convenio","Nome do convênio?"))

    # 👉 ALTERADO: especialidade vem aqui
    campos += [
        ("especialidade","Qual especialidade?"),
        ("nome","Informe seu nome completo:"),
        ("cpf","Informe seu CPF:"),
        ("nasc","Data de nascimento (dd/mm/aaaa):"),
        ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
        ("numero","Informe o número:")
    ]
    return campos

def _comuns_exames(d):
    campos = [("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio":
        campos.append(("convenio","Nome do convênio?"))
    campos += [
        ("nome","Informe seu nome completo:"),
        ("cpf","Informe seu CPF:"),
        ("nasc","Data de nascimento (dd/mm/aaaa):"),
        ("exame","Qual exame?"),
        ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
        ("numero","Informe o número:")
    ]
    return campos

def _fields_for(route, d):
    if route=="consulta":         return _comuns_consulta(d)
    if route=="exames":           return _comuns_exames(d)
    if route=="editar_endereco":  return [("cep","Informe seu CEP:"),("numero","Informe o número:")]
    if route=="retorno":          return [("cpf","Informe o CPF:"), ("nasc","Data de nascimento (dd/mm/aaaa):")]
    if route=="resultado":        return [("cpf","Informe o CPF:"), ("nasc","Data de nascimento (dd/mm/aaaa):")]
    return None

def _question_for(route: str, key: str, d: Dict[str, Any]) -> str:
    fields = _fields_for(route, d) or []
    for k, q in fields:
        if k == key:
            return q
    return "Por favor, informe o dado solicitado."

# ===== Fechamentos ============================================================
FECHAMENTO = {
    "consulta":"✅ Obrigado! Atendente entrará em contato para confirmar a consulta.",
    "exames":"✅ Perfeito! Atendente falará com você para agendar o exame."
}

# ===== Handler principal (Webhook) ============================================
def responder_evento_mensagem(entry: dict) -> None:
    ss = _gspread()

    val      = (entry.get("changes") or [{}])[0].get("value", {})
    messages = val.get("messages", [])
    contacts = val.get("contacts", [])

    if not messages or not contacts:
        return

    msg          = messages[0]
    wa_to        = contacts[0].get("wa_id") or msg.get("from")
    profile_name = (contacts[0].get("profile") or {}).get("name") or ""
    mtype        = msg.get("type")

    # ===== INTERACTIVE =======================================================
    if mtype == "interactive":
        inter    = msg.get("interactive", {})
        br       = inter.get("button_reply") or {}
        lr       = inter.get("list_reply") or {}
        bid_id   = (br.get("id") or lr.get("id") or "").strip()
        # bid_text = (br.get("title") or lr.get("title") or "").strip()  # opcional p/ logs

        if not bid_id:
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

        # ----- raiz (Menu 1)
        if bid_id == "op_consulta":
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            _ask_forma(wa_to)
            return

        if bid_id == "op_exames":
            SESS[wa_to] = {"route":"exames","stage":"forma","data":{"tipo":"exames"}}
            _ask_forma(wa_to)
            return

        # + Opções do Menu 1 → abre Menu 2
        if bid_id == "op_mais":
            SESS[wa_to] = {"route":"mais2","stage":"","data":{}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS_2)
            return

        # ----- Menu 2
        if bid_id == "op_retorno":
            SESS[wa_to] = {"route":"retorno","stage":"cpf","data":{"tipo":"retorno"}}
            _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:")
            return

        if bid_id == "op_resultado":
            SESS[wa_to] = {"route":"resultado","stage":"cpf","data":{"tipo":"resultado"}}
            _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:")
            return

        # + Opções do Menu 2 → abre Menu 3
        if bid_id == "op_mais3":
            SESS[wa_to] = {"route":"mais3","stage":"","data":{}}
            _send_buttons(wa_to, "Mais opções:", BTN_MAIS_3)
            return

        # ----- Menu 3
        if bid_id == "op_endereco":
            txt = (
                "📍 *Endereço*\n"
                "Rua Utrecht, 129 – Vila Rio Branco – CEP 03878-000 – São Paulo/SP\n\n"
                f"🌐 *Site*: {LINK_SITE}\n"
                f"📷 *Instagram*: {LINK_INSTAGRAM}\n"
                "📘 *Facebook*: Clinica Luma\n"
                "☎️ *Telefone*: (11) 2043-9937\n"
                "💬 *WhatsApp*: https://wa.me/5511968501810\n"
                "✉️ *E-mail*: luma.centromed@gmail.com\n"
            )
            _send_text(wa_to, txt)
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return

        if bid_id == "op_editar_endereco":
            # Mantido fluxo original de atualizar dados gerais via 'consulta'
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            _send_text(wa_to, "Vamos atualizar seus dados. Primeiro:")
            _ask_forma(wa_to)
            return

        if bid_id == "op_mais4":
            SESS[wa_to] = {"route":"mais4","stage":"","data":{}}
            _send_buttons(wa_to, "Opções finais:", BTN_MAIS_4)
            return

        if bid_id == "op_sugestoes":
            _send_text(wa_to, MSG_SUGESTOES)
            _send_buttons(wa_to, "Selecione uma opção:", [
                {"id":"sug_especialidades","title":"Especialidades"},
                {"id":"sug_exames","title":"Exames"},
                {"id":"op_voltar_root","title":"Voltar ao início"},
            ])
            return

        if bid_id == "op_voltar_root":
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

        if bid_id == "sug_especialidades":
            SESS[wa_to] = {"route":"sugestao","stage":"await_text","data":{"categoria":"especialidades"}}
            _send_text(wa_to, "Digite quais *especialidades* você gostaria que a clínica oferecesse:")
            return

        if bid_id == "sug_exames":
            SESS[wa_to] = {"route":"sugestao","stage":"await_text","data":{"categoria":"exames"}}
            _send_text(wa_to, "Digite quais *exames* você gostaria que a clínica oferecesse:")
            return

        if bid_id in {"forma_convenio","forma_particular"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            ses["data"]["forma"] = "Convênio" if bid_id=="forma_convenio" else "Particular"
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        if bid_id in {"pac_voce","pac_outro"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            if bid_id == "pac_voce":
                ses["stage"] = None
                SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
                return
            else:
                ses["data"]["_pac_outro"] = True
                ses["stage"] = "paciente_nome"
                SESS[wa_to] = ses
                _send_text(wa_to, "Nome completo do paciente:")
                return

        if bid_id in {"pacdoc_sim","pacdoc_nao"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            if bid_id == "pacdoc_sim":
                ses["stage"] = "paciente_doc"
                SESS[wa_to] = ses
                _send_text(wa_to, "Informe o CPF ou RG do paciente:")
                return
            else:
                ses["data"]["paciente_documento"] = "Não possui"
                ses["stage"] = None
                SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(_gspread(), wa_to, ses)
                return

        if bid_id in {"confirmar","corrigir"}:
            ses = SESS.get(wa_to) or {"route":"root","stage":"","data":{}}
            if bid_id == "corrigir":
                SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
                _send_text(wa_to, "Sem problemas! Vamos corrigir. Primeiro:")
                _ask_forma(wa_to)  
                return
            ses["data"]["_confirmado"] = True
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        if bid_id == "compl_sim":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return

        if bid_id == "compl_nao":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["data"]["complemento"] = ""
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return

    # ===== TEXTO ==============================================================
    if mtype == "text":
        body = (msg.get("text", {}).get("body") or "").strip()
        low  = body.lower()

        # Decisão "paciente possui CPF/RG?" por TEXTO (Sim/Não)
        ses_tmp = SESS.get(wa_to)
        if ses_tmp and ses_tmp.get("route") in {"consulta","exames"} and ses_tmp.get("stage") == "paciente_doc_choice":
            if _is_yes(low):
                ses_tmp["stage"] = "paciente_doc"
                SESS[wa_to] = ses_tmp
                _send_text(wa_to, "Informe o CPF ou RG do paciente:")
                return
            if _is_no(low):
                ses_tmp["data"]["paciente_documento"] = "Não possui"
                ses_tmp["stage"] = None
                SESS[wa_to] = ses_tmp
                _finaliza_ou_pergunta_proximo(_gspread(), wa_to, ses_tmp)
                return

        # sugestões: aguardando texto
        ses = SESS.get(wa_to)
        if ses and ses.get("route") == "sugestao" and ses.get("stage") == "await_text":
            categoria = ses["data"].get("categoria","")
            texto = body.strip()
            if not texto:
                _send_text(wa_to, "Pode digitar sua sugestão, por favor?")
                return
            _add_sugestao(ss, categoria, texto, wa_to)
            _send_text(wa_to, "🙏 Obrigado pela sugestão! Ela nos ajuda a melhorar a cada dia.")
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return

        ses = SESS.get(wa_to)
        active_routes = {"consulta","exames","retorno","resultado","pesquisa","editar_endereco"}
        if ses and ses.get("route") in active_routes and ses.get("stage"):
            _continue_form(_gspread(), wa_to, ses, body)
            return

        # atalhos por texto
        if "consulta" in low:
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            _ask_forma(wa_to); return

        if "exame" in low:
            SESS[wa_to] = {"route":"exames","stage":"forma","data":{"tipo":"exames"}}
            _ask_forma(wa_to); return

        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return
# ===== PARTE 3 =================================================================
# Auxiliares de Fluxo
def _finaliza_ou_pergunta_proximo(ss, wa_to, ses):
    route = ses.get("route")
    data  = ses.get("data", {})

    # Monta endereço quando já houver CEP + número + (complemento definido ou vazio)
    if route in {"consulta","exames","editar_endereco"}:
        if data.get("cep") and data.get("numero") and ("complemento" in data) and not data.get("endereco"):
            end = _montar_endereco_via_cep(data["cep"], data["numero"], data.get("complemento",""))
            if end:
                data["endereco"] = end
            else:
                ses["stage"] = "cep"
                SESS[wa_to] = ses
                _send_text(wa_to, "Não localizei o CEP. Envie 8 dígitos ou informe o endereço completo.")
                return

    # Bifurcação do paciente (em 'consulta' só depois da especialidade)
    if route == "consulta" and data.get("forma") and data.get("especialidade") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True
        ses["stage"] = "paciente_escolha"
        SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE)
        return

    # Mantém comportamento original para 'exames'
    if route == "exames" and data.get("forma") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True
        ses["stage"] = "paciente_escolha"
        SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE)
        return

    # Confirmação antes de salvar (consulta/exames)
    fields = _fields_for(route, data) or []
    pend   = [(k, q) for (k, q) in fields if not data.get(k)]
    if not pend and route in {"consulta","exames"} and not data.get("_confirmado"):
        resumo = [
            f"Responsável: {data.get('nome','')}",
            f"CPF: {data.get('cpf','')}  Nascimento: {data.get('nasc','')}",
            f"Forma: {data.get('forma','')}  Convênio: {data.get('convenio','') or '-'}",
        ]
        if data.get("_pac_outro"):
            resumo += [
                f"Paciente: {data.get('paciente_nome','')}  Nasc: {data.get('paciente_nasc','')}  Doc: {data.get('paciente_documento','') or '-'}"
            ]
        if route=="consulta":
            resumo.append(f"Especialidade: {data.get('especialidade','')}")
        if route=="exames":
            resumo.append(f"Exame: {data.get('exame','')}")
        _send_text(wa_to, "✅ Confirme seus dados:\n" + "\n".join(resumo))
        _send_buttons(wa_to, "Está correto?", BTN_CONFIRMA)
        ses["stage"] = "confirmar"
        SESS[wa_to] = ses
        return

    # Checar pendências (perguntar próximo campo)
    if pend:
        next_key, question = pend[0]
        ses["stage"] = next_key
        SESS[wa_to] = ses
        if next_key == "forma":
            _ask_forma(wa_to); return
        _send_text(wa_to, question)
        return

    # Fluxos retorno/resultado: finalizar simples (CPF + nasc)
    if route in {"retorno","resultado"}:
        _add_solicitacao(ss, data)
        _send_text(wa_to, "✅ Recebido! Nossa equipe vai verificar e te retornar.")
        SESS[wa_to] = {"route":"root","stage":"","data":{}}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    # editar_endereco → apenas registra atualização
    if route == "editar_endereco":
        d = dict(data); d["tipo"] = "editar_endereco"
        _add_solicitacao(ss, d)
        _send_text(wa_to, f"✅ Endereço atualizado e registrado:\n{data.get('endereco','')}")
        SESS[wa_to] = {"route":"root","stage":"","data":data}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    # Sem pendências: salvar (consulta/exames)
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)
    _send_text(wa_to, FECHAMENTO.get(route, "Solicitação registrada."))

    # 🔒 Encerramento: para CONSULTA não reabrir o menu
    if route == "consulta":
        SESS[wa_to] = {"route":"root", "stage":"", "data":{}}
        return

    # Mantém comportamento atual para outros fluxos (ex.: exames)
    SESS[wa_to] = {"route":"root", "stage":"", "data":{}}
    _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)

# Continue form (inclui complemento e paciente "outro")
def _continue_form(ss, wa_to, ses, user_text):
    route = ses["route"]
    stage = ses.get("stage","")
    data  = ses["data"]

    # 1) Campo atual → normaliza/valida/salva
    if stage:
        if stage in {"nasc", "cep"}:
            user_text = _normalize(stage, user_text)

        if stage == "forma":
            data["forma"] = _normalize("forma", user_text)
        else:
            err = _validate(stage, user_text, data=data)
            if err:
                _send_text(wa_to, err)
                _send_text(wa_to, _question_for(route, stage, data))
                return

            data[stage] = user_text if stage in {"nasc", "cep"} else _normalize(stage, user_text)

            # Após CEP válido, perguntar NÚMERO imediatamente
            if stage == "cep" and route in {"consulta","exames","editar_endereco"}:
                ses["stage"] = "numero"
                SESS[wa_to] = ses
                _send_text(wa_to, "Informe o número:")
                return

    # 2) Coleta do paciente quando for "outro"
    if data.get("_pac_outro"):
        if stage == "paciente_nome":
            data["paciente_nome"] = (user_text or "").strip()
            ses["stage"] = "paciente_nasc"
            SESS[wa_to] = ses
            _send_text(wa_to, "Data de nascimento do paciente (dd/mm/aaaa):")
            return
        if stage == "paciente_nasc":
            txt = _normalize("nasc", user_text)
            err = _validate("nasc", txt)
            if err:
                _send_text(wa_to, err)
                _send_text(wa_to, "Data de nascimento do paciente (dd/mm/aaaa):")
                return
            data["paciente_nasc"] = txt
            ses["stage"] = "paciente_doc_choice"
            SESS[wa_to] = ses
            _send_buttons(wa_to, "O paciente possui CPF ou RG?", BTN_PAC_DOC)
            return
        if stage == "paciente_doc":
            data["paciente_documento"] = (user_text or "").strip()
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

    # 3) Após número → perguntar complemento (botões + fallback em texto)
    if route in {"consulta","exames","editar_endereco"} and stage == "numero":
        # garante que o número foi salvo e não está vazio
        if not data.get("numero"):
            _send_text(wa_to, "Informe o número (ou S/N):")
            return
        ses["stage"] = "complemento_decisao"
        SESS[wa_to] = ses
    # Envia um texto curto + os botões; isso força o WhatsApp a renderizar imediatamente
        _send_text(wa_to, "Possui complemento (apto, bloco, sala)? Responda pelos botões ou digite Sim/Não.")
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        return

    # 4) Decisão de complemento via TEXTO (sim/nao)
    if stage == "complemento_decisao":
        if _is_no(user_text):
            data["complemento"] = ""
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return
        if _is_yes(user_text):
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        return

    # 5) Texto do complemento → finaliza
    if stage == "complemento":
        data["complemento"] = (user_text or "").strip()
        ses["stage"] = None
        SESS[wa_to] = ses
        _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
        return

    # 6) Fluxos de pesquisa
    if route == "pesquisa":
        needed = ["nome","cpf","nasc","endereco","especialidade","exame"]
        for k in needed:
            if not data.get(k):
                ses["stage"] = k
                SESS[wa_to] = ses
                _send_text(wa_to, {
                    "nome":"Informe seu nome completo:",
                    "cpf":"Informe seu CPF:",
                    "nasc":"Data de nascimento:",
                    "endereco":"Informe seu endereço completo:",
                    "especialidade":"Qual especialidade você procura?",
                    "exame":"Qual exame você procura?"
                }[k])
                return
        _add_pesquisa(ss, data)
        _send_text(wa_to, "Obrigado! Pesquisa registrada.")
        SESS[wa_to] = {"route":"root","stage":"","data":{}}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    # 7) Continuação padrão
    _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
