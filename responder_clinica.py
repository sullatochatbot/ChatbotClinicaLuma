# responder_clinica.py — Clínica Luma (Especialidades: lista numerada por texto; Exames: lista numerada)
# ==============================================================================
import os, re, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, List

# ===== Variáveis de ambiente ==================================================
WA_ACCESS_TOKEN    = os.getenv("WA_ACCESS_TOKEN", "").strip() or os.getenv("ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "").strip() or os.getenv("PHONE_NUMBER_ID", "").strip()
CLINICA_SHEETS_URL    = os.getenv("CLINICA_SHEETS_URL", "").strip()
CLINICA_SHEETS_SECRET = os.getenv("CLINICA_SHEETS_SECRET", "").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Luma").strip()
LINK_SITE      = os.getenv("LINK_SITE", "https://www.lumaclinicadafamilia.com.br").strip()
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "https://www.instagram.com/luma_clinicamedica").strip()

GRAPH_URL = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages" if WA_PHONE_NUMBER_ID else ""
HEADERS   = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"}

# Evitar duplicatas no mesmo minuto (memória do processo)
_ULTIMAS_CHAVES = set()

# Sessão expira após X minutos sem interação
SESSION_TTL_MIN = 10  # ajuste se quiser

# ===== Persistência via WebApp (novo) ========================================
def _post_webapp(payload: dict) -> dict:
    """Envia JSON para o WebApp (rota 'captacao')."""
    if not (CLINICA_SHEETS_URL and CLINICA_SHEETS_SECRET):
        print("[SHEETS] Config ausente (CLINICA_SHEETS_URL/SECRET).")
        return {"ok": False, "erro": "config ausente"}
    data = {"secret": CLINICA_SHEETS_SECRET, "rota": "captacao"}
    data.update(payload)
    try:
        r = requests.post(CLINICA_SHEETS_URL, json=data, timeout=12)
        r.raise_for_status()
        j = r.json()
        print("[SHEETS] resp:", j)
        return j
    except Exception as e:
        print("[SHEETS] erro:", e)
        return {"ok": False, "erro": str(e)}

def _map_to_captacao(d: dict) -> dict:
    """
    Converte o 'data' do fluxo para os campos do WebApp,
    preenchendo corretamente paciente (E:F:G) e responsável (H:I:J).
    """
    forma = (d.get("forma") or "").strip().lower()
    convenio = (d.get("convenio") or d.get("operadora") or d.get("plano") or "").strip()
    # se houver nome de convênio, garantimos tipo='convenio'
    if convenio:
        tipo = "convenio"
    else:
        tipo = "convenio" if "conv" in forma else ("particular" if "part" in forma else "")
    espec_ex = d.get("especialidade") or d.get("exame") or d.get("tipo") or ""

    # Helpers
    def only_digits(s):
        return "".join(ch for ch in (s or "") if ch.isdigit())

    if d.get("_pac_outro"):
        # Outro paciente → campos específicos
        pac_nome = (d.get("paciente_nome") or "").strip()
        pac_cpf  = only_digits(d.get("paciente_cpf") or d.get("paciente_documento") or "")
        pac_nasc = (d.get("paciente_nasc") or "").strip()

        # Responsável é quem está no WhatsApp
        resp_nome = (d.get("nome") or "").strip()
        resp_cpf  = only_digits(d.get("cpf") or "")
        resp_nasc = (d.get("nasc") or "").strip()
    else:
        # Eu mesmo(a)
        pac_nome = (d.get("nome") or "").strip()
        pac_cpf  = only_digits(d.get("cpf") or "")
        pac_nasc = (d.get("nasc") or "").strip()

        resp_nome = ""
        resp_cpf  = ""
        resp_nasc = ""

    # >>> Novos campos de marketing (somente captação_chatbot)
    origem_cliente      = (d.get("origem_cliente") or "").strip()
    indicador_nome      = (d.get("indicador_nome") or "").strip()
    panfleto_codigo     = (d.get("panfleto_codigo") or "").strip()
    panfleto_codigo_raw = (d.get("panfleto_codigo_raw") or "").strip()

    return {
        "fone": (d.get("contato") or "").strip(),
        "nome_cap": (d.get("whatsapp_nome") or "").strip(),
        "especialidade_exame": espec_ex,

        # >>> campos que o intake usa para preencher a coluna D
        "tipo": tipo,                       # "convenio" ou "particular"
        "tipo_atendimento": tipo,           # redundância (o intake aceita)
        "forma_atendimento": tipo,          # redundância (o intake aceita)
        "convenio": convenio,               # nome do convênio (ex.: "Unimed")
        "convenio_nome": convenio,          # redundância (o intake aceita)

        # Paciente
        "paciente_nome": pac_nome,
        "paciente_cpf":  pac_cpf,
        "paciente_nasc": pac_nasc,

        # Responsável
        "responsavel_nome": resp_nome,
        "responsavel_cpf":  resp_cpf,
        "responsavel_nasc": resp_nasc,

        # Endereço
        "cep": (d.get("cep") or "").strip(),
        "endereco": (d.get("endereco") or "").strip(),
        "numero": (d.get("numero") or "").strip(),
        "complemento": (d.get("complemento") or "").strip(),

        # Marketing
        "origem_cliente": origem_cliente,
        "indicador_nome": indicador_nome,
        "panfleto_codigo": panfleto_codigo,
        "panfleto_codigo_raw": panfleto_codigo_raw,

        "auto_refino": True,
    }

# Mantém as assinaturas usadas no resto do código:
def _upsert_paciente(ss, d): return
def _add_solicitacao(ss, d):
    # chave simples: fone + item + forma + minuto
    chave = f"{(d.get('contato') or '').strip()}|" \
            f"{(d.get('especialidade') or d.get('exame') or '').strip()}|" \
            f"{(d.get('forma') or '').strip()}|" \
            f"{_hora_sp()[:16]}"

    if chave in _ULTIMAS_CHAVES:
        print("[SHEETS] skip duplicate:", chave)
        return
    _ULTIMAS_CHAVES.add(chave)

    _post_webapp(_map_to_captacao(d))
def _add_pesquisa(ss, d):
    dd = dict(d)
    dd["tipo"] = dd.get("tipo") or "pesquisa"
    if dd.get("especialidade"):
        dd["especialidade"] = f"Pesquisa: {dd['especialidade']}"
    elif dd.get("exame"):
        dd["exame"] = f"Pesquisa: {dd['exame']}"
    _post_webapp(_map_to_captacao(dd))
def _add_sugestao(ss, categoria: str, texto: str, wa_id: str):
    print("[Sugestao]", categoria, texto, wa_id)

# ===== Utilitários ============================================================
def _hora_sp():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")

def _now_sp():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))

_RE_CEP = re.compile(r"^\d{8}$")
def _cep_ok(s): return bool(_RE_CEP.match(re.sub(r"\D","",s or "")))

def _via_cep(cep):
    cep = re.sub(r"\D","",cep or "")
    try:
        r = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=4)
        if r.status_code >= 300: return None
        j = r.json()
        if j.get("erro"): return None
        return j
    except: return None

def _montar_endereco_via_cep(cep, numero, complemento=""):
    data = _via_cep(cep)
    if not data: return None
    log = (data.get("logradouro") or ""); bai = (data.get("bairro") or "")
    cid = (data.get("localidade") or ""); uf  = (data.get("uf") or "")
    cep_num = re.sub(r"\D","",cep or "")
    cep_fmt = f"{cep_num[:5]}-{cep_num[5:]}" if len(cep_num)==8 else cep_num
    comp = f" - {complemento.strip()}" if complemento else ""
    return f"{log}, {numero}{comp} - {bai} - {cid}/{uf} – CEP {cep_fmt}".strip()

def _send_text(to: str, text: str):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA TEXT]", to, text); return
    payload = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"preview_url":False,"body":text[:4096]}}
    requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

def _send_buttons(to: str, body: str, buttons: List[Dict[str,str]]):
    btns = buttons[:3]  # WhatsApp: máx 3
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA BTNS]", to, body, btns); return
    payload = {
        "messaging_product":"whatsapp","to":to,"type":"interactive",
        "interactive":{"type":"button","body":{"text":body[:1024]},"action":{"buttons":[{"type":"reply","reply":b} for b in btns]}}
    }
    requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

# ===== Botões/UI ==============================================================
WELCOME_GENERIC = f"Bem-vindo à {NOME_EMPRESA}! Escolha uma opção abaixo para começar."
def _welcome_named(name):
    return (f"Bem-vindo(a), {name.split()[0]}! Este é o atendimento virtual da {NOME_EMPRESA}."
            if name else WELCOME_GENERIC)

BTN_ROOT = [{"id": "op_consulta", "title": "Consulta"},
            {"id": "op_exames",   "title": "Exames"},
            {"id": "op_mais",     "title": "+ Opções"}]

BTN_MAIS_2 = [{"id": "op_retorno",   "title": "Retorno de consultas"},
              {"id": "op_resultado", "title": "Resultado de exames"},
              {"id": "op_mais3",     "title": "+ Opções"}]

BTN_MAIS_3 = [{"id": "op_endereco",        "title": "Endereço"},
              {"id": "op_editar_endereco", "title": "Editar dados gerais"},
              {"id": "op_mais4",           "title": "+ Opções"}]

BTN_MAIS_4 = [{"id": "op_sugestoes",   "title": "Sugestões"},
              {"id": "op_voltar_root", "title": "Voltar ao início"}]

BTN_FORMA = [{"id": "forma_convenio", "title": "Convênio"},
             {"id": "forma_particular", "title": "Particular"}]

BTN_COMPLEMENTO = [{"id": "compl_sim", "title": "Sim"},
                   {"id": "compl_nao", "title": "Não"}]

BTN_CONFIRMA = [{"id": "confirmar", "title": "Confirmar"},
                {"id": "corrigir",  "title": "Corrigir"}]

BTN_PACIENTE = [{"id": "pac_voce",  "title": "Eu mesmo(a)"},
                {"id": "pac_outro", "title": "Outro paciente"}]

BTN_PAC_DOC = [{"id": "pacdoc_sim", "title": "Sim"},
               {"id": "pacdoc_nao", "title": "Não"}]

MSG_SUGESTOES = ("💡 Ajude a Clínica Luma a melhorar! Diga quais *especialidades* ou *exames* "
                 "você gostaria que tivéssemos.")

# ===== Catálogos / Especialidades e Exames ===================================
# Especialidades — já era lista numerada
ESPECIALIDADES_ORDER = [
    "Clínico Geral","Dermatologia e Estética","Endocrinologia",
    "Fonoaudiologia","Medicina do Trabalho",
    "Ortopedia","Pediatria","Psiquiatria","Terapia ABA",
]
def _especialidade_menu_texto():
    linhas = ["Escolha a especialidade digitando o *número* correspondente:"]
    for i, nome in enumerate(ESPECIALIDADES_ORDER, start=1):
        linhas.append(f"{i:>2}) {nome}")
    linhas.append("\nEx.: digite 1 para Clínico Geral, 7 para Pediatria, etc.")
    return "\n".join(linhas)
def _ask_especialidade_num(wa_to, ses):
    ses["stage"] = "especialidade_num"; SESS[wa_to] = ses
    _send_text(wa_to, _especialidade_menu_texto())

# Exames — agora também como lista numerada (entrada SOMENTE por número)
EXAMES_ORDER = [
    "Exames Laboratoriais",
    "Eletrocardiograma"
    "Raio X",
    # Adicione novos exames aqui mantendo o rótulo canônico que você deseja ver no Sheets
]
def _exame_menu_texto():
    linhas = ["Escolha o exame digitando o *número* correspondente:"]
    for i, nome in enumerate(EXAMES_ORDER, start=1):
        linhas.append(f"{i:>2}) {nome}")
    linhas.append("\nEx.: digite 1 para Exames Laboratoriais, 2 para Raio X.")
    return "\n".join(linhas)
def _ask_exame_num(wa_to, ses):
    ses["stage"] = "exame_num"; SESS[wa_to] = ses
    _send_text(wa_to, _exame_menu_texto())

# ===== Validadores e normalização ============================================
_RE_CPF  = re.compile(r"\D")
def _cpf_clean(s): return _RE_CPF.sub("", s or "")

def _date_ok(s: str) -> bool:
    try:
        raw = (s or "").strip(); dig = re.sub(r"\D", "", raw)
        if len(dig) == 8:
            datetime.strptime(f"{dig[:2]}/{dig[2:4]}/{dig[4:]}", "%d/%m/%Y"); return True
        datetime.strptime(raw.replace("-", "/"), "%d/%m/%Y"); return True
    except Exception: return False

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
    if key=="cpf": return _cpf_clean(v)
    if key=="forma":
        l = v.lower()
        if "conv" in l: return "Convênio"
        if "part" in l: return "Particular"
    if key == "nasc":
        s = re.sub(r"\D", "", v)
        if len(s) == 8: return f"{s[:2]}/{s[2:4]}/{s[4:]}"
        return (v or "").replace("-", "/")
    if key == "cep": return re.sub(r"\D", "", v)[:8]
    return v

def _ask_forma(to): _send_buttons(to, "Convênio ou Particular?", BTN_FORMA)

# ===== Origem menu (marketing) ===============================================
def _origem_menu_texto():
    return (
        "Antes de encerrar, onde você nos conheceu?\n"
        "1) Instagram\n"
        "2) Facebook\n"
        "3) Google\n"
        "4) Indicação\n"
        "5) Panfleto (P=)\n"
        "0) Pular\n\n"
        "Digite apenas o número da opção:"
    )

def _normalize_panfleto(raw: str) -> (str, str):
    """Normaliza qualquer entrada para 'P=1234' se houver dígitos; retorna (normalizado, raw)."""
    raw = (raw or "").strip()
    dig = re.sub(r"\D", "", raw)
    if dig:
        dig = dig.lstrip("0") or "0"
        return f"P={dig}", raw
    return "", raw

# ===== Sessão ================================================================
SESS: Dict[str, Dict[str, Any]] = {}

# ===== Campos dinâmicos / Fluxo ==============================================
def _comuns_consulta(d):
    campos = [("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio": campos.append(("convenio","Nome do convênio?"))
    campos += [("especialidade","Qual especialidade?"),
               ("nome","Informe seu nome completo:"),
               ("cpf","Informe seu CPF:"),
               ("nasc","Data de nascimento (dd/mm/aaaa):"),
               ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
               ("numero","Informe o número:")]
    return campos

def _comuns_exames(d):
    campos = [("forma","Convênio ou Particular?")]
    if d.get("forma")=="Convênio": campos.append(("convenio","Nome do convênio?"))
    campos += [("exame","Qual exame?"),
               ("nome","Informe seu nome completo:"),
               ("cpf","Informe seu CPF:"),
               ("nasc","Data de nascimento (dd/mm/aaaa):"),
               ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
               ("numero","Informe o número:")]
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
        if k == key: return q
    return "Por favor, informe o dado solicitado."

FECHAMENTO = {
    "consulta":"✅ Obrigado! Por favor, aguarde que uma atendente entrará em contato para confirmar a consulta.",
    "exames":"✅ Perfeito! Por favor, aguarde que uma atendente entrará em contato com você para agendar o exame."
}

# ===== Handler principal ======================================================
def responder_evento_mensagem(entry: dict) -> None:
    ss = None  # persistência via WebApp — não precisamos de conexão local ao Sheets

    val      = (entry.get("changes") or [{}])[0].get("value", {})
    messages = val.get("messages", [])
    contacts = val.get("contacts", [])
    if not messages or not contacts: return

    msg          = messages[0]
    wa_to        = contacts[0].get("wa_id") or msg.get("from")
    profile_name = (contacts[0].get("profile") or {}).get("name") or ""
    mtype        = msg.get("type")

    # cria/recupera sessão
    ses = SESS.setdefault(wa_to, {"route":"root","stage":"","data":{}, "last_at": None})
    ses["data"]["contato"] = wa_to
    ses["data"]["whatsapp_nome"] = profile_name

    # TTL: se passou do tempo, reinicia do zero
    try:
        now  = _now_sp()
        last = ses.get("last_at")
        if last and (now - last).total_seconds() > SESSION_TTL_MIN * 60:
            SESS[wa_to] = {"route":"root","stage":"","data":{}, "last_at": now}
            _send_buttons(wa_to, "Reiniciei seu atendimento para começarmos do zero 👇", BTN_ROOT)
            return
        ses["last_at"] = now
    except Exception:
        ses["last_at"] = _now_sp()

    # ===== INTERACTIVE =======================================================
    if mtype == "interactive":
        inter    = msg.get("interactive", {})
        br       = inter.get("button_reply") or {}
        lr       = inter.get("list_reply") or {}
        bid_id   = (br.get("id") or lr.get("id") or "").strip()
        if not bid_id:
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

        # Menu raiz
        if bid_id == "op_consulta":
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            _ask_forma(wa_to); return
        if bid_id == "op_exames":
            SESS[wa_to] = {"route":"exames","stage":"forma","data":{"tipo":"exames"}}
            _ask_forma(wa_to); return

        # + Opções → Menus adicionais
        if bid_id == "op_mais":
            SESS[wa_to] = {"route":"mais2","stage":"","data":{}}
            _send_buttons(wa_to, "Outras opções:", BTN_MAIS_2); return
        if bid_id == "op_retorno":
            SESS[wa_to] = {"route":"retorno","stage":"cpf","data":{"tipo":"retorno"}}
            _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:"); return
        if bid_id == "op_resultado":
            SESS[wa_to] = {"route":"resultado","stage":"cpf","data":{"tipo":"resultado"}}
            _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:"); return
        if bid_id == "op_mais3":
            SESS[wa_to] = {"route":"mais3","stage":"","data":{}}
            _send_buttons(wa_to, "Mais opções:", BTN_MAIS_3); return
        if bid_id == "op_endereco":
            txt = (
                "📍 *Endereço*\n"
                "Rua Utrecht, 129 – Vila Rio Branco – CEP 03878-000 – São Paulo/SP\n\n"
                f"🌐 *Site*: {LINK_SITE}\n"
                f"📷 *Instagram*: {LINK_INSTAGRAM}\n"
                "📘 *Facebook*: Clinica Luma\n"
                "☎️ *Telefone*: (11) 2043-9937\n"
                "💬 *WhatsApp*: https://wa.me/5511968501810\n"
                "💬 *WhatsApp*: https://wa.me/5511975379655\n"
                "✉️ *E-mail*: luma.centromed@gmail.com\n"
            )
            _send_text(wa_to, txt)
            _send_buttons(wa_to, "Posso ajudar em algo mais?", BTN_ROOT); return
        if bid_id == "op_editar_endereco":
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            _send_text(wa_to, "Vamos atualizar seus dados. Primeiro:")
            _ask_forma(wa_to); return
        if bid_id == "op_mais4":
            SESS[wa_to] = {"route":"mais4","stage":"","data":{}}
            _send_buttons(wa_to, "Opções finais:", BTN_MAIS_4); return
        if bid_id == "op_sugestoes":
            _send_text(wa_to, MSG_SUGESTOES)
            _send_buttons(wa_to, "Selecione uma opção:", [
                {"id":"sug_especialidades","title":"Especialidades"},
                {"id":"sug_exames","title":"Exames"},
                {"id":"op_voltar_root","title":"Voltar ao início"},
            ]); return
        if bid_id == "op_voltar_root":
            SESS[wa_to] = {"route":"root","stage":"","data":{}}
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

        # Sugestões
        if bid_id == "sug_especialidades":
            SESS[wa_to] = {"route":"sugestao","stage":"await_text","data":{"categoria":"especialidades"}}
            _send_text(wa_to, "Digite quais *especialidades* você gostaria que a clínica oferecesse:"); return
        if bid_id == "sug_exames":
            SESS[wa_to] = {"route":"sugestao","stage":"await_text","data":{"categoria":"exames"}}
            _send_text(wa_to, "Digite quais *exames* você gostaria que a clínica oferecesse:"); return

        # (Removido) EXAMES por botões: agora é lista numerada via texto

        # Forma / paciente / doc / confirmar
        if bid_id in {"forma_convenio","forma_particular"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            ses["data"]["forma"] = "Convênio" if bid_id=="forma_convenio" else "Particular"
            if ses.get("route") == "consulta":
                if ses["data"]["forma"] == "Convênio" and not ses["data"].get("convenio"):
                    ses["stage"] = "convenio"; SESS[wa_to] = ses
                    _send_text(wa_to, "Qual o nome do convênio?"); return
                _ask_especialidade_num(wa_to, ses); return
            if ses.get("route") == "exames":
                if ses["data"]["forma"] == "Convênio" and not ses["data"].get("convenio"):
                    ses["stage"] = "convenio"; SESS[wa_to] = ses
                    _send_text(wa_to, "Qual o nome do convênio?"); return
                _ask_exame_num(wa_to, ses); return
            SESS[wa_to] = ses; _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        if bid_id in {"pac_voce","pac_outro"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            if bid_id == "pac_voce":
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            else:
                ses["data"]["_pac_outro"] = True; ses["stage"] = "paciente_nome"; SESS[wa_to] = ses
                _send_text(wa_to, "Nome completo do paciente:"); return

        if bid_id in {"pacdoc_sim","pacdoc_nao"}:
            ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
            if bid_id == "pacdoc_sim":
                ses["stage"] = "paciente_doc"; SESS[wa_to] = ses
                _send_text(wa_to, "Informe o CPF ou RG do paciente:"); return
            else:
                ses["data"]["paciente_documento"] = "Não possui"
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        if bid_id in {"confirmar","corrigir"}:
            ses = SESS.get(wa_to) or {"route":"root","stage":"","data":{}}
            if bid_id == "corrigir":
                tipo_atual = (ses.get("data") or {}).get("tipo") or ("consulta" if ses.get("route")=="consulta" else "exames")
                nova_route = "exames" if tipo_atual == "exames" else "consulta"
                SESS[wa_to] = {"route": nova_route, "stage": "forma", "data": {"tipo": nova_route}}
                _send_text(wa_to, "Sem problemas! Vamos corrigir. Primeiro:"); _ask_forma(wa_to); return
            ses["data"]["_confirmado"] = True; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        if bid_id == "compl_sim":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["stage"] = "complemento"; SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):"); return
        if bid_id == "compl_nao":
            ses = SESS.get(wa_to) or {"route":"", "stage":"", "data":{}}
            ses["data"]["complemento"] = ""; ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

    # ===== TEXTO ==============================================================
    if mtype == "text":
        body = (msg.get("text", {}).get("body") or "").strip()
        low  = body.lower()

        # reset manual da conversa
        if low in {"menu", "inicio", "início", "reiniciar", "start", "começar"}:
            SESS[wa_to] = {"route":"root","stage":"","data":{}, "last_at": _now_sp()}
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

        # decisões simples por texto (quando bot perguntou)
        ses_tmp = SESS.get(wa_to)
        if ses_tmp and ses_tmp.get("route") in {"consulta","exames"} and ses_tmp.get("stage") == "paciente_doc_choice":
            if low in {"sim","s","yes","y"}:
                ses_tmp["stage"] = "paciente_doc"; SESS[wa_to] = ses_tmp
                _send_text(wa_to, "Informe o CPF ou RG do paciente:"); return
            if low in {"nao","não","n","no"}:
                ses_tmp["data"]["paciente_documento"] = "Não possui"
                ses_tmp["stage"] = None; SESS[wa_to] = ses_tmp
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses_tmp); return

        # sugestões aguardando texto
        ses = SESS.get(wa_to)
        if ses and ses.get("route") == "sugestao" and ses.get("stage") == "await_text":
            categoria = ses["data"].get("categoria",""); texto = body.strip()
            if not texto:
                _send_text(wa_to, "Pode digitar sua sugestão, por favor?"); return
            _add_sugestao(ss, categoria, texto, wa_to)
            _send_text(wa_to, "🙏 Obrigado pela sugestão! Ela nos ajuda a melhorar a cada dia.")
            SESS[wa_to] = {"route":"root","stage":"","data":{}}; return

        # ====== ORIGEM (marketing) — menu numerado / coleta P= =================
        ses = SESS.get(wa_to)
        if ses and ses.get("stage") == "origem_menu":
            escolha = re.sub(r"\D", "", body or "")
            if not escolha:
                _send_text(wa_to, "Por favor, digite apenas um número (0 a 5).")
                _send_text(wa_to, _origem_menu_texto()); return
            op = int(escolha)
            if op == 0:
                ses["data"]["origem_cliente"] = ""
                ses["data"]["_origem_done"] = True
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            if op == 1:
                ses["data"]["origem_cliente"] = "Instagram"
                ses["data"]["_origem_done"] = True
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            if op == 2:
                ses["data"]["origem_cliente"] = "Facebook"
                ses["data"]["_origem_done"] = True
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            if op == 3:
                ses["data"]["origem_cliente"] = "Google"
                ses["data"]["_origem_done"] = True
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            if op == 4:
                ses["data"]["origem_cliente"] = "Indicação"
                ses["stage"] = "indicador_nome"; SESS[wa_to] = ses
                _send_text(wa_to, "Quem indicou? (pode pular digitando 0)"); return
            if op == 5:
                ses["data"]["origem_cliente"] = "Panfleto"
                ses["stage"] = "panfleto_codigo"; SESS[wa_to] = ses
                _send_text(wa_to, "Digite o código exatamente como impresso (ex.: P=1234).\n"
                                  "Dica: se só tiver números, pode mandar assim mesmo (ex.: 1234)."); return
            _send_text(wa_to, "Opção inválida. Escolha um número entre 0 e 5.")
            _send_text(wa_to, _origem_menu_texto()); return

        if ses and ses.get("stage") == "indicador_nome":
            if body.strip() and body.strip() != "0":
                ses["data"]["indicador_nome"] = body.strip()
            else:
                ses["data"]["indicador_nome"] = ""
            ses["data"]["_origem_done"] = True
            ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        if ses and ses.get("stage") == "panfleto_codigo":
            code_norm, code_raw = _normalize_panfleto(body)
            ses["data"]["panfleto_codigo"] = code_norm
            ses["data"]["panfleto_codigo_raw"] = code_raw
            ses["data"]["_origem_done"] = True
            ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
        # ======================================================================

        # ====== EXAMES por número =============================================
        if ses and ses.get("route") == "exames" and ses.get("stage") == "exame_num":
            txt = (body or "").strip()
            m = re.match(r"^\s*(\d{1,2})\s*$", txt)
            if not m:
                _send_text(wa_to, "Por favor, digite apenas o número do exame.")
                _send_text(wa_to, _exame_menu_texto()); return
            idx = int(m.group(1))
            if not (1 <= idx <= len(EXAMES_ORDER)):
                _send_text(wa_to, f"O número {idx} não está na lista. Tente novamente.")
                _send_text(wa_to, _exame_menu_texto()); return
            ses["data"]["exame"] = EXAMES_ORDER[idx-1]
            ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
        # ======================================================================

        # fluxo ativo por texto
        ses = SESS.get(wa_to)
        active_routes = {"consulta","exames","retorno","resultado","pesquisa","editar_endereco"}
        if ses and ses.get("route") in active_routes and ses.get("stage"):
            _continue_form(ss, wa_to, ses, body); return
        ses = SESS.get(wa_to)
        if ses and ses.get("route") in active_routes and not ses.get("stage"):
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        # atalhos
        if "consulta" in low:
            SESS[wa_to] = {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}; _ask_forma(wa_to); return
        if "exame" in low:
            SESS[wa_to] = {"route":"exames","stage":"forma","data":{"tipo":"exames"}}; _ask_forma(wa_to); return

        _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT); return

# ===== Decidir próximo passo / salvar ========================================
def _finaliza_ou_pergunta_proximo(ss, wa_to, ses):
    route = ses.get("route"); data  = ses.get("data", {})

    # Completar endereço via CEP
    if route in {"consulta","exames","editar_endereco"}:
        if data.get("cep") and data.get("numero") and ("complemento" in data) and not data.get("endereco"):
            end = _montar_endereco_via_cep(data["cep"], data["numero"], data.get("complemento",""))
            if end: data["endereco"] = end
            else:
                ses["stage"] = "cep"; SESS[wa_to] = ses
                _send_text(wa_to, "Não localizei o CEP. Envie 8 dígitos ou informe o endereço completo."); return

    # Bifurcação paciente após escolha de forma+especialidade/exame
    if route == "consulta" and data.get("forma") and data.get("especialidade") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True; ses["stage"] = "paciente_escolha"; SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE); return
    if route == "exames" and data.get("forma") and data.get("exame") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True; ses["stage"] = "paciente_escolha"; SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE); return

    fields = _fields_for(route, data) or []
    pend   = [(k, q) for (k, q) in fields if not data.get(k)]
    if not pend and route in {"consulta","exames"} and not data.get("_confirmado"):
        resumo = [
            f"Responsável: {data.get('nome','')}",
            f"CPF: {data.get('cpf','')}  Nascimento: {data.get('nasc','')}",
            f"Forma: {data.get('forma','')}  Convênio: {data.get('convenio','') or '-'}",
        ]
        if data.get("_pac_outro"):
            resumo += [f"Paciente: {data.get('paciente_nome','')}  Nasc: {data.get('paciente_nasc','')}  Doc: {data.get('paciente_documento','') or '-'}"]
        if route=="consulta": resumo.append(f"Especialidade: {data.get('especialidade','')}")
        if route=="exames":   resumo.append(f"Exame: {data.get('exame','')}")
        _send_text(wa_to, "✅ Confirme seus dados:\n" + "\n".join(resumo))
        _send_buttons(wa_to, "Está correto?", BTN_CONFIRMA)
        ses["stage"] = "confirmar"; SESS[wa_to] = ses; return

    if pend:
        next_key, question = pend[0]
        ses["stage"] = next_key; SESS[wa_to] = ses
        if next_key == "forma": _ask_forma(wa_to); return
        if route == "consulta" and next_key == "especialidade": _ask_especialidade_num(wa_to, ses); return
        if route == "exames"   and next_key == "exame":          _ask_exame_num(wa_to, ses); return
        _send_text(wa_to, question); return

    if route in {"retorno","resultado"}:
        _add_solicitacao(ss, data)
        _send_text(wa_to, "✅ Recebido! Nossa equipe vai verificar e te retornar.")
        SESS[wa_to] = {"route":"root","stage":"","data":{}}; return

    if route == "editar_endereco":
        d = dict(data); d["tipo"] = "editar_endereco"
        _add_solicitacao(ss, d)
        _send_text(wa_to, f"✅ Endereço atualizado e registrado:\n{data.get('endereco','')}")
        SESS[wa_to] = {"route":"root","stage":"","data":data}; return

    # >>> Gancho de marketing antes de salvar (apenas uma vez)
    if (route in {"consulta","exames"}) and data.get("_confirmado") and not data.get("_origem_done"):
        ses["stage"] = "origem_menu"; SESS[wa_to] = ses
        _send_text(wa_to, _origem_menu_texto()); return

    # Salvar e encerrar
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)
    _send_text(wa_to, FECHAMENTO.get(route, "Solicitação registrada."))
    SESS[wa_to] = {"route":"root", "stage":"", "data":{}}

# ===== Continue form ==========================================================
def _continue_form(ss, wa_to, ses, user_text):
    route = ses["route"]; stage = ses.get("stage",""); data  = ses["data"]

    # Reabrir UI correta se aguardando
    if (route == "consulta" and stage == "especialidade"): _ask_especialidade_num(wa_to, ses); return
    if (route == "exames" and stage == "exame_num"):       _ask_exame_num(wa_to, ses); return

    # Campo atual
    if stage:
        if stage in {"nasc", "cep"}: user_text = _normalize(stage, user_text)
        if stage == "forma": data["forma"] = _normalize("forma", user_text)
        else:
            # casos especiais (marketing) já tratados fora
            if stage not in {"indicador_nome", "panfleto_codigo", "origem_menu", "exame_num"}:
                err = _validate(stage, user_text, data=data)
                if err:
                    _send_text(wa_to, err); _send_text(wa_to, _question_for(route, stage, data)); return
                data[stage] = user_text if stage in {"nasc", "cep"} else _normalize(stage, user_text)
                if route == "consulta" and stage == "convenio":
                    _ask_especialidade_num(wa_to, ses); return
                if route == "exames" and stage == "convenio":
                    _ask_exame_num(wa_to, ses); return
                if stage == "cep" and route in {"consulta","exames","editar_endereco"}:
                    ses["stage"] = "numero"; SESS[wa_to] = ses; _send_text(wa_to, "Informe o número:"); return

    # Paciente "outro"
    if data.get("_pac_outro"):
        if stage == "paciente_nome":
            data["paciente_nome"] = (user_text or "").strip()
            ses["stage"] = "paciente_nasc"; SESS[wa_to] = ses
            _send_text(wa_to, "Data de nascimento do paciente (dd/mm/aaaa):"); return
        if stage == "paciente_nasc":
            txt = _normalize("nasc", user_text); err = _validate("nasc", txt)
            if err:
                _send_text(wa_to, err); _send_text(wa_to, "Data de nascimento do paciente (dd/mm/aaaa):"); return
            data["paciente_nasc"] = txt
            ses["stage"] = "paciente_doc_choice"; SESS[wa_to] = ses
            _send_buttons(wa_to, "O paciente possui CPF ou RG?", BTN_PAC_DOC); return
        if stage == "paciente_doc":
            data["paciente_documento"] = (user_text or "").strip()
            ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

    # Endereço
    if route in {"consulta","exames","editar_endereco"} and stage == "numero":
        if not data.get("numero"):
            _send_text(wa_to, "Informe o número (ou S/N):"); return
        ses["stage"] = "complemento_decisao"; SESS[wa_to] = ses
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO); return

    if stage == "complemento_decisao":
        l = (user_text or "").strip().lower()
        if l in {"nao","não","n","no"}:
            data["complemento"] = ""; ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
        if l in {"sim","s","yes","y"}:
            ses["stage"] = "complemento"; SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):"); return
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO); return

    if stage == "complemento":
        data["complemento"] = (user_text or "").strip()
        ses["stage"] = None; SESS[wa_to] = ses
        _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

    # Especialidade por número
    if route == "consulta" and stage == "especialidade_num":
        txt = (user_text or "").strip()
        m = re.match(r"^\s*(\d{1,2})\s*$", txt)
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(ESPECIALIDADES_ORDER):
                ses["data"]["especialidade"] = ESPECIALIDADES_ORDER[idx-1]
                ses["stage"] = None; SESS[wa_to] = ses
                _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return
            _send_text(wa_to, f"O número {idx} não está na lista. Tente novamente.")
            _send_text(wa_to, _especialidade_menu_texto()); return
        _send_text(wa_to, "Não entendi. Digite apenas o número da especialidade.")
        _send_text(wa_to, _especialidade_menu_texto()); return

    # Pesquisa (se usar)
    if route == "pesquisa":
        needed = ["nome","cpf","nasc","endereco","especialidade","exame"]
        for k in needed:
            if not data.get(k):
                ses["stage"] = k; SESS[wa_to] = ses
                _send_text(wa_to, {
                    "nome":"Informe seu nome completo:",
                    "cpf":"Informe seu CPF:",
                    "nasc":"Data de nascimento:",
                    "endereco":"Informe seu endereço completo:",
                    "especialidade":"Qual especialidade você procura?",
                    "exame":"Qual exame você procura?"
                }[k]); return
        _add_pesquisa(ss, data); _send_text(wa_to, "Obrigado! Pesquisa registrada.")
        SESS[wa_to] = {"route":"root","stage":"","data":{}}; return

    # Continuação padrão
    _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
