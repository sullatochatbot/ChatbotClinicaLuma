# responder_clinica.py — Clínica Luma
# ==============================================================================
# ==== PARTE 1 =================================================================
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
def _cep_ok(s): return bool(_RE_CEP.match(re.sub(r"\D","",s or "")))

def _via_cep(cep):
    cep = re.sub(r"\D","",cep or "")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=10)
        if r.status_code >= 300: return None
        j = r.json()
        if j.get("erro"): return None
        return j
    except: return None

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
    return f"Bem-vindo(a), {name.split()[0]}! Este é o atendimento virtual da {NOME_EMPRESA}." if name else WELCOME_GENERIC

BTN_ROOT = [
    {"id": "op_consulta", "title": "Consulta"},
    {"id": "op_exames",   "title": "Exames"},
    {"id": "op_mais",     "title": "+ Opções"},
]
# ==== PARTE 2 =================================================================

BTN_MAIS_2 = [
    {"id": "op_retorno",    "title": "Retorno de consultas"},
    {"id": "op_resultado",  "title": "Resultado de exames"},
    {"id": "op_mais3",      "title": "+ Opções"}
]

BTN_MAIS_3 = [
    {"id": "op_endereco",        "title": "Endereço"},
    {"id": "op_editar_endereco", "title": "Editar dados gerais"},
    {"id": "op_mais4",           "title": "+ Opções"}
]

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

def _cpf_clean(s): return _RE_CPF.sub("", s or "")
def _cpf_ok(s): return len(_cpf_clean(s)) == 11

def _normalize(key, v):
    v = (v or "").strip()
    if key=="cpf": return _cpf_clean(v)
    if key=="forma":
        l = v.lower()
        if "conv" in l: return "Convênio"
        if "part" in l: return "Particular"
    if key=="nasc":
        s = re.sub(r"\D","",v)
        if len(s)==8: return f"{s[:2]}/{s[2:4]}/{s[4:]}"
        return v
    if key=="cep": return re.sub(r"\D","",v)[:8]
    return v

def _validate(key, v, *, data=None):
    v = (v or "").strip()
    if key=="cpf" and not _cpf_ok(v): return "CPF inválido (11 dígitos)."
    if key=="nasc" and len(re.sub(r"\D","",v))!=8: return "Data inválida. Use o formato dd/mm/aaaa."
    if key=="convenio" and (data or {}).get("forma")=="Convênio" and not v: return "Informe o convênio."
    if key=="cep" and not _cep_ok(v): return "CEP inválido (8 dígitos)."
    if key=="numero" and not v: return "Informe o número."
    if key in {"forma","nome","especialidade","exame"} and not v: return "Obrigatório."
    return None

def _ask_forma(to):
    _send_buttons(to, "Convênio ou Particular?", BTN_FORMA)

# ===== YES/NO ================================================================
def _is_yes(txt: str) -> bool: return (txt or "").strip().lower() in {"sim","s","yes","y"}
def _is_no(txt: str)  -> bool: return (txt or "").strip().lower() in {"nao","não","n","no"}

# ===== Sessão ================================================================
SESS: Dict[str, Dict[str, Any]] = {}

# ===== Campos ================================================================
def _comuns_consulta(d):
    campos = [("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio":
        campos.append(("convenio","Nome do convênio?"))
    # Mudança: perguntar especialidade ANTES do nome
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
    # Mudança: perguntar exame ANTES do nome
    campos += [
        ("exame","Qual exame?"),
        ("nome","Informe seu nome completo:"),
        ("cpf","Informe seu CPF:"),
        ("nasc","Data de nascimento (dd/mm/aaaa):"),
        ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
        ("numero","Informe o número:")
    ]
    return campos
# ============================================
# PARTE 3 — Auxiliares de Fluxo + Continue Form
# ============================================

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
                # CEP inválido → volta para CEP (evita “travamento”)
                ses["stage"] = "cep"
                SESS[wa_to] = ses
                _send_text(wa_to, "Não localizei o CEP. Envie 8 dígitos (ex: 03878000) ou informe o endereço completo.")
                return

    # Bifurcação paciente (logo após definirmos 'forma')
    if route in {"consulta","exames"} and data.get("forma") and not data.get("_pac_decidido"):
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
            resumo.append(
                f"Paciente: {data.get('paciente_nome','')}  Nasc: {data.get('paciente_nasc','')}  Doc: {data.get('paciente_documento','') or '-'}"
            )
        if route == "consulta":
            resumo.append(f"Especialidade: {data.get('especialidade','')}")
        if route == "exames":
            resumo.append(f"Exame: {data.get('exame','')}")
        _send_text(wa_to, "✅ Confirme seus dados:\n" + "\n".join(resumo))
        _send_buttons(wa_to, "Está correto?", BTN_CONFIRMA)
        ses["stage"] = "confirmar"
        SESS[wa_to] = ses
        return

    # Checar pendências → perguntar próximo campo
    if pend:
        next_key, question = pend[0]
        ses["stage"] = next_key
        SESS[wa_to] = ses
        if next_key == "forma":
            _ask_forma(wa_to)
            return
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
    SESS[wa_to] = {"route":"root", "stage":"", "data":{}}
    _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT)


def _continue_form(ss, wa_to, ses, user_text):
    """
    Avança o formulário campo a campo, com foco em:
      - Não travar após CPF, nascimento, número, complemento.
      - Reperguntar automaticamente em caso de erro.
      - Fallback para SIM/NÃO por texto quando os botões não carregarem.
    """
    route = ses["route"]
    stage = ses.get("stage","")
    data  = ses["data"]

    # 1) Campo atual → normaliza/valida/salva
    if stage:
        # Normalização antes de validar (evita erro bobo prender o fluxo)
        if stage in {"nasc", "cep"}:
            user_text = _normalize(stage, user_text)

        if stage == "forma":
            data["forma"] = _normalize("forma", user_text)
        else:
            err = _validate(stage, user_text, data=data)
            if err:
                # Re-ask automático do mesmo campo (anti-trava)
                _send_text(wa_to, err)
                _send_text(wa_to, _question_for(route, stage, data))
                return

            # Salva valor normalizado
            data[stage] = user_text if stage in {"nasc", "cep"} else _normalize(stage, user_text)

            # Após CEP válido, perguntar NÚMERO imediatamente (anti-limbo)
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
            _send_text(wa_to, "Se os botões não aparecerem, digite: *Sim* ou *Não*.")  # fallback
            return

        if stage == "paciente_doc":
            data["paciente_documento"] = (user_text or "").strip()
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

    # 3) Após número → perguntar complemento (botões) + fallback por texto
    if route in {"consulta","exames","editar_endereco"} and stage == "numero":
        ses["stage"] = "complemento_decisao"
        SESS[wa_to] = ses
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        _send_text(wa_to, "Se os botões não aparecerem, digite: *Sim* ou *Não*.")  # fallback
        return

    # 4) Decisão de complemento via TEXTO (sim/nao digitado)
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
        # Resposta inválida → reexibe opções (anti-trava)
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        _send_text(wa_to, "Se preferir, digite: *Sim* ou *Não*.")
        return

    # 5) Texto do complemento → finaliza endereço e segue
    if stage == "complemento":
        data["complemento"] = (user_text or "").strip()
        ses["stage"] = None
        SESS[wa_to] = ses
        _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
        return

    # 6) Fluxo de pesquisa (quando usado)
    if route == "pesquisa":
        needed = ["nome","cpf","nasc","endereco","especialidade","exame"]
        for k in needed:
            if not data.get(k):
                ses["stage"] = k
                SESS[wa_to] = ses
                _send_text(wa_to, {
                    "nome":"Informe seu nome completo:",
                    "cpf":"Informe seu CPF:",
                    "nasc":"Data de nascimento (dd/mm/aaaa):",
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

    # 7) Continuação padrão (sem travar)
    _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
