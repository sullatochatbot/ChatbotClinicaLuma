# responder_clinica.py — Clínica Luma
# ==============================================================================

import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional

WA_ACCESS_TOKEN         = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "").strip()
CLINICA_SHEET_ID        = os.getenv("CLINICA_SHEET_ID", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Luma").strip()
LINK_SITE      = os.getenv("LINK_SITE", "https://www.lumaclinicadafamilia.com.br").strip()
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "https://www.instagram.com/luma_clinicamedica").strip()

GRAPH_URL = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages" if WA_PHONE_NUMBER_ID else ""
HEADERS   = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}

# ==== Google Sheets ===========================================================
import gspread
from google.oauth2.service_account import Credentials

def _gspread():
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
    return ss

def _ensure_ws(ss, title, headers):
    try: ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows="1000", cols=str(len(headers)+2))
        ws.insert_row(headers, index=1)
    else:
        if ws.row_values(1) != headers:
            ws.resize(rows=max(ws.row_count, 1000), cols=len(headers))
            ws.update(f"A1:{chr(64+len(headers))}1", [headers])

# ==== Utilitários =============================================================
def _hora_sp(): return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")

_RE_CEP = re.compile(r"^\d{8}$")
def _cep_ok(s): return bool(_RE_CEP.match(re.sub(r"\D","",s or "")))

def _via_cep(cep):
    cep = re.sub(r"\D","",cep or "")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/",timeout=10)
        if r.status_code>=300: return None
        j = r.json()
        if j.get("erro"): return None
        return j
    except: return None

def _montar_endereco_via_cep(cep, numero, complemento=""):
    data = _via_cep(cep)
    if not data: return None
    log,bai,cid,uf = (data.get("logradouro") or ""), (data.get("bairro") or ""), (data.get("localidade") or ""), (data.get("uf") or "")
    cep_num = re.sub(r"\D","",cep or "")
    cep_fmt = f"{cep_num[:5]}-{cep_num[5:]}" if len(cep_num)==8 else cep_num
    return f"{log}, {numero}{(' - '+complemento) if complemento else ''} - {bai} - {cid}/{uf} – CEP {cep_fmt}".strip()

def _send_text(to, text):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID): print("[MOCK→WA TEXT]",to,text); return
    payload={"messaging_product":"whatsapp","to":to,"type":"text","text":{"preview_url":False,"body":text[:4096]}}
    requests.post(GRAPH_URL,headers=HEADERS,json=payload,timeout=30)

def _send_buttons(to, body, buttons):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID): print("[MOCK→WA BTNS]",to,body,buttons); return
    payload={"messaging_product":"whatsapp","to":to,"type":"interactive",
             "interactive":{"type":"button","body":{"text":body[:1024]},
                            "action":{"buttons":[{"type":"reply","reply":b} for b in buttons[:3]]}}}
    requests.post(GRAPH_URL,headers=HEADERS,json=payload,timeout=30)

# ==== Botões/UI ===============================================================
WELCOME_GENERIC=f"Bem-vindo à {NOME_EMPRESA}! Escolha uma opção abaixo para começar."
def _welcome_named(name): return f"Bem-vindo(a), {name.split()[0]}! Este é o atendimento virtual da {NOME_EMPRESA}." if name else WELCOME_GENERIC

BTN_ROOT=[{"id":"op_consulta","title":"Consulta"},{"id":"op_exames","title":"Exames"},{"id":"op_mais","title":"+ Opções"}]
BTN_MAIS_1=[
    {"id":"op_endereco","title":"Endereço"},
    {"id":"op_contato","title":"Contato"},
    {"id":"op_editar_endereco","title":"Editar endereço"},
    {"id":"op_mais2","title":"+ Opções"},
]
BTN_MAIS_2=[{"id":"op_especialidade","title":"Especialidade"},{"id":"op_exames_atalho","title":"Exames"},{"id":"op_voltar_root","title":"Voltar"}]
BTN_FORMA=[{"id":"forma_convenio","title":"Convênio"},{"id":"forma_particular","title":"Particular"}]
BTN_COMPLEMENTO=[{"id":"compl_sim","title":"Sim"},{"id":"compl_nao","title":"Não"}]
# ==== Validadores =============================================================
_RE_CPF=re.compile(r"\D"); _RE_DATE=re.compile(r"^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/\d{4}$")
def _cpf_clean(s): return _RE_CPF.sub("",s or "")
def _date_ok(s): return bool(_RE_DATE.match(s or ""))

def _validate(key,v,*,data=None):
    v=(v or "").strip()
    if key=="cpf" and len(_cpf_clean(v))!=11: return "CPF inválido."
    if key=="nasc" and not _date_ok(v): return "Data inválida."
    if key=="convenio" and (data or {}).get("forma")=="Convênio" and not v: return "Informe o convênio."
    if key=="cep" and not _cep_ok(v): return "CEP inválido."
    if key=="numero" and not v: return "Informe o número."
    if key in {"forma","nome","especialidade","exame"} and not v: return "Obrigatório."
    return None

def _normalize(key,v):
    v=(v or "").strip()
    if key=="cpf": return _cpf_clean(v)
    if key=="forma":
        l=v.lower()
        if "conv" in l: return "Convênio"
        if "part" in l: return "Particular"
    return v

def _ask_forma(to): _send_buttons(to,"Convênio ou Particular?",BTN_FORMA)

# ==== Persistência ============================================================
def _find_paciente(ss,cpf):
    ws=ss.worksheet("Pacientes"); col=ws.col_values(1)
    try: idx=col.index(cpf)+1
    except ValueError: return None
    return dict(zip(ws.row_values(1),ws.row_values(idx)))

def _upsert_paciente(ss,d):
    ws=ss.worksheet("Pacientes"); cpf=d.get("cpf"); 
    if not cpf or cpf in ws.col_values(1): return
    ws.append_row([d.get("cpf",""),d.get("nome",""),d.get("nasc",""),d.get("endereco",""),
                   d.get("cep",""),d.get("numero",""),d.get("complemento",""),
                   d.get("forma",""),d.get("convenio",""),d.get("tipo",""),_hora_sp()],
                  value_input_option="USER_ENTERED")

def _add_solicitacao(ss,d):
    ws=ss.worksheet("Solicitacoes")
    ws.append_row([_hora_sp(),d.get("tipo",""),d.get("forma",""),d.get("convenio",""),d.get("cpf",""),
                   d.get("nome",""),d.get("nasc",""),d.get("especialidade",""),d.get("exame",""),
                   d.get("endereco",""),d.get("cep",""),d.get("numero",""),d.get("complemento","")],
                  value_input_option="USER_ENTERED")

def _add_pesquisa(ss,d):
    ws=ss.worksheet("Pesquisa")
    ws.append_row([_hora_sp(),d.get("cpf",""),d.get("nome",""),d.get("nasc",""),d.get("endereco",""),
                   d.get("cep",""),d.get("numero",""),d.get("complemento",""),
                   d.get("especialidade",""),d.get("exame","")],
                  value_input_option="USER_ENTERED")

# ==== Sessão ================================================================
SESS:Dict[str,Dict[str,Any]]={}

# ==== Campos dinâmicos ======================================================
def _comuns_consulta(d):
    campos=[("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio": campos.append(("convenio","Nome do convênio?"))
    campos+=[("nome","Informe seu nome completo:"),("cpf","Informe seu CPF:"),("nasc","Data de nascimento:"),("especialidade","Qual especialidade?"),("cep","Informe seu CEP:"),("numero","Informe o número:")]
    return campos

def _comuns_exames(d):
    campos=[("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio": campos.append(("convenio","Nome do convênio?"))
    campos+=[("nome","Informe seu nome completo:"),("cpf","Informe seu CPF:"),("nasc","Data de nascimento:"),("exame","Qual exame?"),("cep","Informe seu CEP:"),("numero","Informe o número:")]
    return campos

def _fields_for(route,d):
    if route=="consulta": return _comuns_consulta(d)
    if route=="exames": return _comuns_exames(d)
    if route=="editar_endereco": return [("cep","Informe seu CEP:"),("numero","Informe o número:")]
    return None

# ==== Fechamentos ===========================================================
FECHAMENTO={"consulta":"✅ Obrigado! Atendente entrará em contato para confirmar a consulta.",
            "exames":"✅ Perfeito! Atendente falará com você para agendar o exame."}
# ====== Handler principal =====================================================
def responder_evento_mensagem(entry: dict) -> None:
    ss = _gspread()
    val = (entry.get("changes") or [{}])[0].get("value", {})
    messages = val.get("messages", [])
    contacts = val.get("contacts", [])
    if not messages or not contacts:
        return

    msg = messages[0]
    wa_to = contacts[0].get("wa_id") or msg.get("from")
    profile_name = (contacts[0].get("profile") or {}).get("name") or ""
    mtype = msg.get("type")

    # -- INTERACTIVE -----------------------------------------------------------
    if mtype == "interactive":
        inter = msg.get("interactive", {})
        br = inter.get("button_reply") or {}
        lr = inter.get("list_reply") or {}
        bid = br.get("id") or br.get("title") or lr.get("id") or lr.get("title")

        if not bid:
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

        # raiz
        if bid in {"op_consulta", "Consulta"}:
            SESS[wa_to] = {
                "route": "consulta",
                "stage": "forma",
                "data": {"tipo": "consulta"},
            }
            _ask_forma(wa_to)
            return

        if bid in {"op_exames", "Exames"}:
            SESS[wa_to] = {
                "route": "exames",
                "stage": "forma",
                "data": {"tipo": "exames"},
            }
            _ask_forma(wa_to)
            return

        if bid in {"op_mais", "+ Opções", "+ Opcoes"}:
            SESS[wa_to] = {"route": "mais", "stage": "", "data": {}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS_1)
            return

        # opções nível 1
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
            if LINK_SITE:
                txt += f"• Site: {LINK_SITE}\n"
            if LINK_INSTAGRAM:
                txt += f"• Instagram: {LINK_INSTAGRAM}\n"
            txt = txt.strip() or "Em breve canais de contato."
            _send_text(wa_to, txt)
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
            return

        if bid == "op_editar_endereco":
            atual = SESS.get(wa_to) or {"route": "root", "stage": "", "data": {}}
            SESS[wa_to] = {
                "route": "editar_endereco",
                "stage": "cep",
                "data": dict(atual.get("data", {})),
            }
            _send_text(
                wa_to,
                "Informe seu CEP (apenas números, ex: 03878000):"
            )
            return

        if bid == "op_mais2":
            _send_buttons(wa_to, "O que você procura?", BTN_MAIS_2)
            return

        if bid == "op_voltar_root":
            SESS[wa_to] = {"route": "root", "stage": "", "data": {}}
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

        # submenu pesquisa
        if bid == "op_especialidade":
            ses = {"route": "pesquisa", "stage": "especialidade", "data": {}}
            SESS[wa_to] = ses
            _send_text(wa_to, "Qual especialidade você procura?")
            return

        if bid == "op_exames_atalho":
            ses = {"route": "pesquisa", "stage": "exame", "data": {}}
            SESS[wa_to] = ses
            _send_text(wa_to, "Qual exame você procura?")
            return

        # forma (convênio/particular)
        if bid in {"forma_convenio", "forma_particular"}:
            ses = SESS.get(wa_to) or {
                "route": "consulta",
                "stage": "forma",
                "data": {"tipo": "consulta"},
            }
            ses["data"]["forma"] = (
                "Convênio" if bid == "forma_convenio" else "Particular"
            )
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        # complemento sim/não
        if bid == "compl_sim":
            ses = SESS.get(wa_to) or {"route": "", "stage": "", "data": {}}
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return

        if bid == "compl_nao":
            ses = SESS.get(wa_to) or {"route": "", "stage": "", "data": {}}
            ses["data"]["complemento"] = ""
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        # fallback
        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return

    # -- TEXTO -----------------------------------------------------------------
    if mtype == "text":
        body = (msg.get("text", {}).get("body") or "").strip()
        low = body.lower()

        ses = SESS.get(wa_to)
        active_routes = {
            "consulta", "exames", "retorno",
            "resultado", "pesquisa", "editar_endereco"
        }
        if ses and ses.get("route") in active_routes and ses.get("stage"):
            _continue_form(_gspread(), wa_to, ses, body)
            return

        if "consulta" in low:
            SESS[wa_to] = {
                "route": "consulta",
                "stage": "forma",
                "data": {"tipo": "consulta"},
            }
            _ask_forma(wa_to)
            return

        if "exame" in low:
            SESS[wa_to] = {
                "route": "exames",
                "stage": "forma",
                "data": {"tipo": "exames"},
            }
            _ask_forma(wa_to)
            return

        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
        return

# ====== Auxiliares de Fluxo ===================================================
def _after_forma_prompt_next(wa_to, ses):
    route = ses.get("route")
    data = ses.get("data", {})
    fields = _fields_for(route, data) or []
    pending = [(k, q) for (k, q) in fields if not data.get(k)]
    if pending:
        next_key, question = pending[0]
        if next_key == "forma":
            _ask_forma(wa_to)
        else:
            SESS[wa_to]["stage"] = next_key
            _send_text(wa_to, question)
    else:
        SESS[wa_to]["stage"] = None

# >>> Helper: finaliza ou pergunta o próximo campo (centralizado)
def _finaliza_ou_pergunta_proximo(ss, wa_to, ses):
    route = ses.get("route")
    data = ses.get("data", {})

    # montar endereço (consulta/exames/editar_endereco)
    if route in {"consulta", "exames", "editar_endereco"}:
        comp = (data.get("complemento", "") or "").strip().lower()
        if comp in {"sem", "s/", "s"}:
            data["complemento"] = ""
        has_min = data.get("cep") and data.get("numero")
        has_comp = "complemento" in data
        need_end = has_min and has_comp and not data.get("endereco")
        if need_end:
            end = _montar_endereco_via_cep(
                data["cep"], data["numero"], data.get("complemento", "")
            )
            if end:
                data["endereco"] = end
            else:
                SESS[wa_to]["stage"] = "cep"
                _send_text(
                    wa_to,
                    "Não localizei o CEP. Envie 8 dígitos "
                    "ou informe o endereço completo."
                )
                return

    # checar pendências
    fields = _fields_for(route, data) or []
    pend = [(k, q) for (k, q) in fields if not data.get(k)]
    if pend:
        next_key, question = pend[0]
        SESS[wa_to]["stage"] = next_key
        if next_key == "forma":
            _ask_forma(wa_to)
        else:
            _send_text(wa_to, question)
        return

    # sem pendências: finalizar
    if route == "editar_endereco":
        data_atual = dict(data)
        data_atual["tipo"] = "editar_endereco"
        _add_solicitacao(ss, data_atual)
        _send_text(
            wa_to,
            f"✅ Endereço atualizado e registrado:\n{data.get('endereco','')}"
        )
        SESS[wa_to] = {"route": "root", "stage": "", "data": data}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)
    txt = FECHAMENTO.get(route, "Solicitação registrada.")
    _send_text(wa_to, txt)
    SESS[wa_to] = {"route": "root", "stage": "", "data": {}}
    _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)

def _start_pesquisa(wa_to, ses):
    needed = ["nome", "cpf", "nasc", "endereco"]
    missing = [k for k in needed if not ses["data"].get(k)]
    if missing:
        ses["stage"] = missing[0]
        _send_text(wa_to, _prompt_basico(missing[0]))
    else:
        ses["stage"] = "especialidade"
        _send_text(wa_to, "Qual especialidade você procura?")

def _continue_form(ss, wa_to, ses, user_text):
    route = ses["route"]
    stage = ses.get("stage", "")
    data = ses["data"]

    # valida e salva
    if stage:
        if stage == "forma" and user_text:
            data["forma"] = _normalize("forma", user_text)
        else:
            err = _validate(stage, user_text, data=data)
            if err:
                _send_text(wa_to, err)
                return
            data[stage] = _normalize(stage, user_text)

    # após número → perguntar complemento
    if route in {"consulta", "exames", "editar_endereco"} and stage == "numero":
        SESS[wa_to]["stage"] = "complemento_pending"
        _send_buttons(
            wa_to,
            "Possui complemento (apto, bloco, sala)?",
            BTN_COMPLEMENTO
        )
        return

    # pesquisa (se existir no seu fluxo)
    if route == "pesquisa":
        needed = ["nome", "cpf", "nasc", "endereco", "especialidade", "exame"]
        for k in needed:
            if not data.get(k):
                ses["stage"] = k
                if k in {"nome", "cpf", "nasc", "endereco"}:
                    _send_text(wa_to, _prompt_basico(k))
                else:
                    pergunta = (
                        "Qual especialidade você procura?"
                        if k == "especialidade"
                        else "Qual exame você procura?"
                    )
                    _send_text(wa_to, pergunta)
                return
        _add_pesquisa(ss, data)
        _send_text(wa_to, "Obrigado! Pesquisa registrada.")
        SESS[wa_to] = {"route": "root", "stage": "", "data": {}}
        _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)
        return

    # finalização/continuação centralizada
    _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
