# responder_clinica.py — Clínica Luma (Especialidades: lista numerada por texto; Exames: lista numerada)
# ==============================================================================
import os, re, json, requests
import time
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
SESSION_TTL_MIN = 120

# ===== Persistência via WebApp ===============================================
def _post_webapp(payload: dict) -> dict:
    """
    Envia JSON para o WebApp (rota 'chatbot').
    Garante message_id único, normaliza contato/whatsapp_nome e P/Q/R, e loga o que foi enviado.
    """
    if not (CLINICA_SHEETS_URL and CLINICA_SHEETS_SECRET):
        # >>> ATENÇÃO:
        # Se as variáveis de ambiente não estiverem configuradas no Render,
        # o envio para o Sheets NÃO acontecerá.
        # NÃO comentar o return abaixo.
        print("[SHEETS] Config ausente (CLINICA_SHEETS_URL/SECRET).")
        return {"ok": False, "erro": "config ausente"}

    # Base do payload: rota/secret no BODY (aceitos pelo intake)
    data = {"secret": CLINICA_SHEETS_SECRET, "rota": "chatbot"}
    data.update(payload or {})

    # message_id único (evita dedupe)
    if not data.get("message_id"):
        data["message_id"] = f"auto-{int(datetime.now().timestamp()*1000)}"

    # Normalização contato / whatsapp_nome
    data["contato"] = (
        data.get("contato")
        or data.get("fone")
        or data.get("telefone")
        or data.get("wa_id")   # ← NOVO fallback
        or ""
    )

    data["whatsapp_nome"] = (
        data.get("whatsapp_nome")
        or data.get("nome_whatsapp")
        or data.get("nome_cap")
        or data.get("nome")
        or ""
    )

    # 'forma' (fallback de 'tipo', se for o caso)
    if not data.get("forma") and data.get("tipo"):
        data["forma"] = data.get("tipo")

    # ---------------- P / Q / R ----------------
    # P: origem_cliente
    data["origem_cliente"] = (
        data.get("origem_cliente")
        or data.get("origem")
        or data.get("origemCliente")
        or ""
    )
    # Q: panfleto_codigo
    data["panfleto_codigo"] = (
        data.get("panfleto_codigo")
        or data.get("panfleto_codigo_raw")
        or data.get("origem_panfleto_codigo")
        or data.get("panfletoCodigo")
        or data.get("panfletoCodigoRaw")
        or ""
    )
    # R: origem_outro_texto
    data["origem_outro_texto"] = (
        data.get("origem_outro_texto")
        or data.get("origem_texto")
        or data.get("origemOutroTexto")
        or ""
    )

    # Compatibilidade antiga
    data["origem"]                 = data["origem_cliente"]
    data["origem_panfleto_codigo"] = data["panfleto_codigo"]
    data["origem_texto"]           = data["origem_outro_texto"]

    # Debug enxuto (mostra exatamente o que vai para o Sheets)
    dbg = {k: data.get(k) for k in [
        "message_id","contato","whatsapp_nome","especialidade","exame","forma","tipo",
        "origem_cliente","panfleto_codigo","origem_outro_texto"
    ]}
    print("[SEND→Sheets] url:", CLINICA_SHEETS_URL)
    print("[SEND→Sheets] campos:", json.dumps(dbg, ensure_ascii=False))

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
    Converte o 'data' do fluxo para o payload do WebApp,
    preservando campos originais e adicionando o que o intake espera.
    """
    out = dict(d)  # <<< preserva especialidade/exame/forma/whatsapp_nome etc.

    # Contato / identificação
    out["message_id"]    = d.get("message_id") or out.get("message_id")
    out["contato"]       = (d.get("contato") or "").strip()
    out["whatsapp_nome"] = (d.get("whatsapp_nome") or "").strip()

    # Forma (Convênio/Particular) — intake aceita alias 'forma'/'tipo'
    if d.get("forma"):
        out["forma"] = d.get("forma").strip()
    else:
        # fallback antigo que inferia 'tipo'; mantido só por compatibilidade
        forma_low = (d.get("forma") or "").strip().lower()
        if "conv" in forma_low: out["forma"] = "Convênio"
        elif "part" in forma_low: out["forma"] = "Particular"
    
        # >>> NOVO: para o Sheets, se for Convênio e houver nome do convênio,
        # escrevemos o NOME do convênio na coluna "tipo (Particular ou nome do convênio)"
    if (out.get("forma","").lower().startswith("conv")) and d.get("convenio"):
        out["forma"] = d["convenio"].strip()

    # Paciente / responsável (já vêm de d; garantimos normalização mínima)
    def only_digits(s): return "".join(ch for ch in (s or "") if ch.isdigit())

    # Sempre preencher os 3 campos do RESPONSÁVEL (quem está falando no WhatsApp)
    out["responsavel_nome"] = (d.get("nome") or "").strip()
    out["responsavel_cpf"]  = only_digits(d.get("cpf") or "")
    out["responsavel_nasc"] = (d.get("nasc") or "").strip()

    if d.get("_pac_outro"):
        # Paciente é outra pessoa → manter paciente_* separados
        cpf_pac = (
            d.get("paciente_cpf")
            or d.get("paciente_documento")
            or d.get("cpf_paciente")
            or d.get("doc_paciente")
            or d.get("documento_paciente")
        )
        out["paciente_cpf"] = only_digits(cpf_pac or "")

        # (paciente_nome e paciente_nasc já são coletados no fluxo e vão em out se existirem)
    else:
        # Paciente é o próprio responsável → espelhar nos campos paciente_*
        out["paciente_nome"] = (d.get("nome") or "").strip()
        cpf_self = (
            d.get("cpf")
            or d.get("documento")
            or d.get("paciente_cpf")
            or d.get("cpf_paciente")
        )
        out["paciente_cpf"]  = only_digits(cpf_self or "")
        out["paciente_nasc"] = (d.get("nasc") or "").strip()

    # Marketing P / Q / R
    out["origem_cliente"]      = (d.get("origem_cliente") or d.get("origem") or "").strip()           # P
    out["panfleto_codigo"]     = (d.get("panfleto_codigo") or d.get("panfleto_codigo_raw") or "").strip()  # Q
    out["origem_outro_texto"]  = (d.get("origem_outro_texto") or d.get("origem_texto") or "").strip()  # R

    # Compatibilidade retro (se algum código seu antigo ainda ler esses nomes)
    out["origem"]                 = out["origem_cliente"]
    out["origem_panfleto_codigo"] = out["panfleto_codigo"]
    out["origem_texto"]           = out["origem_outro_texto"]

    # >>> NOVO: sugestões livres (se houver)
    if d.get("sugestao_especialidade"):
        out["sugestao_especialidade"] = (d.get("sugestao_especialidade") or "").strip()
    if d.get("sugestao_exame"):
        out["sugestao_exame"] = (d.get("sugestao_exame") or "").strip()

    # Remova chaves internas que não interessam no Sheets, se houver
    out.pop("_pac_decidido", None)
    out.pop("_origem_done", None)
    out.pop("_compl_decidido", None)

    return out

# Mantém as assinaturas usadas no resto do código:
def _upsert_paciente(ss, d): return

def _add_solicitacao(ss, d):
    # chave simples: fone + item + forma + minuto
    chave = f"{(d.get('contato') or '').strip()}|" \
        f"{(d.get('especialidade') or d.get('exame') or '').strip()}|" \
        f"{(d.get('forma') or '').strip()}"

    if chave in _ULTIMAS_CHAVES:
        print("[SHEETS] skip duplicate:", chave)
    else:
        _ULTIMAS_CHAVES.add(chave)

    # >>> NOVO: dedupe consciente por fluxo (consulta vs exames)
    payload = _map_to_captacao(d)

    # 🔧 GARANTE QUE EXAME OU ESPECIALIDADE SEJA ENVIADO
    payload["especialidade"] = d.get("especialidade") or d.get("exame") or ""

    base = (d.get("wa_id") or d.get("contato") or "").strip()
    tipo = (d.get("tipo") or ("exames" if d.get("exame") else "consulta")).lower()

    payload["dedupe_key"] = f"{base}-{tipo}-{int(time.time())}"

    _post_webapp(payload)

def _add_pesquisa(ss, d):
    dd = dict(d)
    dd["tipo"] = dd.get("tipo") or "pesquisa"
    if dd.get("especialidade"):
        dd["especialidade"] = f"Pesquisa: {dd['especialidade']}"
    elif dd.get("exame"):
        dd["exame"] = f"Pesquisa: {dd['exame']}"
    _post_webapp(_map_to_captacao(dd))

def _add_sugestao(ss, categoria: str, texto: str, wa_id: str):
    # Normaliza entrada
    cat = (categoria or "").lower().strip()
    txt = (texto or "").strip()
    if not txt:
        return

    # Base da linha (identidade + carimbo de data/hora)
    payload = {
        "contato": (wa_id or "").strip(),
        "whatsapp_nome": ((SESS.get(wa_id) or {}).get("data") or {}).get("whatsapp_nome", "").strip(),
        "timestamp_local": _hora_sp(),
        "tipo": "sugestao",        # Coluna E: classificador
    }

    # Preencha D com o campo OFICIAL que o intake entende
    if "exame" in cat and "especial" not in cat:
        payload["exame"] = txt                 # Coluna D (via intake)
    else:
        payload["especialidade"] = txt         # Coluna D (via intake)

    # Dedupe leve por usuário+tipo
    import time
    base = (payload["contato"] or "anon")
    payload["dedupe_key"] = f"{base}-sugestao-{int(time.time())}"

    _post_webapp(payload)

    # Mensagem de fechamento (inalterada)
    try:
        enviar_texto = globals().get("wa_text") or globals().get("send_text") or globals().get("responder_texto")
        if callable(enviar_texto):
            enviar_texto(wa_id, "🙏 Obrigado pela sugestão! Já anotamos aqui. Atendimento encerrado.")
    except Exception as e:
        print("[SUGESTAO] aviso: não consegui enviar msg de encerramento:", e)

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
    btns = buttons[:3]  # WhatsApp permite no máximo 3 botões

    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA BTNS]", to, body, btns)
        return

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": b}
                    for b in btns
                ]
            }
        }
    }

    r = requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

    print("📤 BUTTON STATUS:", r.status_code)
    print("📤 BUTTON RESP:", r.text)

# ===== TEMPLATE COM IMAGEM (HEADER) =========================================

def _normalizar_dropbox(url: str) -> str:
    if not url:
        return ""
    u = url.strip()
    u = u.replace("https://www.dropbox.com", "https://dl.dropboxusercontent.com")
    u = u.replace("?dl=0", "")
    return u

def _send_template_image(to: str, template_name: str, image_url: str, body_params: List[str]):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        print("[MOCK→WA TEMPLATE IMG]", to, template_name, image_url, body_params)
        return

    image_url = _normalizar_dropbox(image_url)

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": { "code": "pt_BR" },
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {
                                "link": image_url
                            }
                        }
                    ]
                },
                {
                    "type": "body",
                    "parameters": [
                        { "type": "text", "text": str(p) } for p in body_params
                    ]
                }
            ]
        }
    }

    r = requests.post(GRAPH_URL, headers=HEADERS, json=payload, timeout=30)

    print("📤 TEMPLATE STATUS:", r.status_code)
    print("📤 TEMPLATE RESP:", r.text)

# ============================================================
# DISPARO TEMPLATE SIMPLES (IGUAL OFICINA)
# ============================================================

def enviar_template_clinica_disparo(numero):
    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": numero,
        "type": "template",
        "template": {
            "name": "teste_img_luma_v1",  # CONFIRME O NOME EXATO
            "language": {
                "code": "pt_BR"
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    print("📤 TEMPLATE CLINICA STATUS:", response.status_code)
    print("📤 TEMPLATE CLINICA BODY:", response.text)

    return response.text

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
ESPECIALIDADES_ORDER = [
    "Clínico Geral",
    "Dermatologia e Estética",
    "Harmonização Facial",
    "Endocrinologia",
    "Fonoaudiologia",
    "Medicina do Trabalho",
    "Nutrólogo / Med. Esportiva * Emagrecimento 30+",
    "Ortopedia",
    "Pediatria",
    "Psiquiatria",
]

def _especialidade_menu_texto():
    linhas = ["Escolha a especialidade digitando o *número* correspondente:"]
    for i, nome in enumerate(ESPECIALIDADES_ORDER, start=1):
        linhas.append(f"{i:>2}) {nome}")
    linhas.append("\nEx.: digite o número correspondente")
    return "\n".join(linhas)

def _ask_especialidade_num(wa_to, ses):
    ses["stage"] = "especialidade_num"; SESS[wa_to] = ses
    _send_text(wa_to, _especialidade_menu_texto())

EXAMES_ORDER = [
    "Admissional / Demissional",   # ← NOVO exame incluído
    "Exames Laboratoriais",
    "Eletrocardiograma",
    "Raio X",
    "Toxicológico - cnh",
    # Adicione novos exames aqui mantendo o rótulo canônico que você deseja ver no Sheets
]

def _exame_menu_texto():
    linhas = ["Escolha o exame digitando o *número* correspondente:"]
    for i, nome in enumerate(EXAMES_ORDER, start=1):
        linhas.append(f"{i:>2}) {nome}")
    linhas.append("\nEx.: por favor, digite o número correspondente ao exame ")
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

# ===== Origem (marketing) =====================================================
def _origem_menu_texto():
    return (
        "Antes de encerrar, onde você nos conheceu?\n"
        "1) Instagram\n"
        "2) Facebook\n"
        "3) Google\n"
        "4) Panfletos (digite código promocional impresso)\n"
        "5) Outros\n"
        "0) Pular\n\n"
        "Digite apenas o número da opção:"
    )

def _normalize_panfleto(raw: str):
    """Normaliza para 'P=1234' se houver dígitos; retorna (normalizado, raw)."""
    raw = (raw or "").strip()
    dig = re.sub(r"\D", "", raw)
    if dig:
        dig = dig.lstrip("0") or "0"
        return f"P={dig}", raw
    return "", raw

# ===== Sessão ================================================================
SESS: Dict[str, Dict[str, Any]] = {}
ACESSOS_DIA: Dict[str, str] = {}

# ============================================================
# RESET DE SESSÃO (IGUAL OFICINA)
# ============================================================
def reset_sessao(numero: str):
    if numero in SESS:
        del SESS[numero]
    if numero in ACESSOS_DIA:
        del ACESSOS_DIA[numero]

# ===== Campos dinâmicos / Fluxo ==============================================
def _comuns_consulta(d):
    campos = [("forma","Convênio ou Particular?")]

    if d.get("forma")=="Convênio":
        campos.append(("convenio","Nome do convênio?"))

    campos += [
        ("especialidade","Qual especialidade?"),
        ("nome","Informe seu nome completo:")
    ]

    # ===== CAMPOS DESATIVADOS TEMPORARIAMENTE =====
    # Para reativar, basta remover o comentário abaixo

    # campos += [
    #     ("cpf","Informe seu CPF:"),
    #     ("nasc","Data de nascimento (dd/mm/aaaa):"),
    #     ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
    #     ("numero","Informe o número:")
    # ]

    return campos

def _comuns_exames(d):
    campos = [("forma","Convênio ou Particular?")]

    if d.get("forma")=="Convênio":
        campos.append(("convenio","Nome do convênio?"))

    campos += [
        ("exame","Qual exame?"),
        ("nome","Informe seu nome completo:")
    ]

    # ===== CAMPOS DESATIVADOS TEMPORARIAMENTE =====
    # Para reativar depois, basta remover o comentário abaixo

    # campos += [
    #     ("cpf","Informe seu CPF:"),
    #     ("nasc","Data de nascimento (dd/mm/aaaa):"),
    #     ("cep","Informe seu CEP (8 dígitos, ex: 03878000):"),
    #     ("numero","Informe o número:")
    # ]

    return campos

def _fields_for(route, d):
    if route=="consulta":         return _comuns_consulta(d)
    if route=="exames":           return _comuns_exames(d)
    if route=="editar_endereco":  return [("cep","Informe seu CEP:"),("numero","Informe o número:")]
    if route=="retorno":

        # ===== CAMPOS ANTIGOS DESATIVADOS TEMPORARIAMENTE =====
        # return [("cpf","Informe o CPF:"), ("nasc","Data de nascimento (dd/mm/aaaa):")]

        # >>> NOVO FLUXO SIMPLIFICADO
        return [("nome","Informe o nome completo do paciente:")]
    
    if route=="resultado":

        # ===== CAMPOS ANTIGOS DESATIVADOS TEMPORARIAMENTE =====
        # return [("cpf","Informe o CPF:"), ("nasc","Data de nascimento (dd/mm/aaaa):")]

        # >>> NOVO FLUXO SIMPLIFICADO
        return [("nome","Informe o nome completo do paciente:")]
    
    return None

def _question_for(route: str, key: str, d: Dict[str, Any]) -> str:
    fields = _fields_for(route, d) or []
    for k, q in fields:
        if k == key: return q
    return "Por favor, informe o dado solicitado."

FECHAMENTO = {
    "consulta":"✅ Obrigado! Seu pedido de consulta foi recebido.\n\n"
               "Uma atendente da Clínica Luma entrará em contato em breve para confirmar.\n\n"
               "📲 O contato será feito pelo número:\n"
               "(11) 97537-9655\n\n"
               "Se preferir falar agora, clique no link abaixo:\n"
               "https://wa.me/5511975379655",

    "exames":"✅ Perfeito! Seu pedido de exame foi recebido.\n\n"
             "Uma atendente da Clínica Luma entrará em contato em breve para realizar o agendamento.\n\n"
             "📲 O contato será feito pelo número:\n"
             "(11) 97537-9655\n\n"
             "Se preferir falar agora, clique no link abaixo:\n"
             "https://wa.me/5511975379655"
}

# ===== Handler principal ======================================================
def responder_evento_mensagem(entry: dict) -> None:
    ss = None

    val      = (entry.get("changes") or [{}])[0].get("value", {})
    messages = val.get("messages", [])
    contacts = val.get("contacts", [])
    if not messages or not contacts:
        return

    msg          = messages[0]
    wa_to        = contacts[0].get("wa_id") or msg.get("from")
    profile_name = (contacts[0].get("profile") or {}).get("name") or ""
    mtype        = msg.get("type")

    # ===== cria/recupera sessão =====
    ses = SESS.get(wa_to)
    now = _now_sp()
    today = now.strftime("%Y-%m-%d")

    if not ses:
        ses = {
            "route": "root",
            "stage": "",
            "data": {},
            "last_at": now
        }
        SESS[wa_to] = ses
    else:
        ses["last_at"] = now

    # 🔐 GARANTIR IDENTIFICAÇÃO DO CONTATO
    ses["data"]["contato"] = wa_to
    ses["data"]["wa_id"] = wa_to
    ses["data"]["whatsapp_nome"] = profile_name

    # ===== REGISTRO DE ACESSO APENAS 1x POR DIA =====
    if ACESSOS_DIA.get(wa_to) != today:
        try:
            _post_webapp({
                "tipo": "acesso_inicial",
                "especialidade": "acesso_inicial",
                "contato": wa_to,
                "whatsapp_nome": profile_name,
                "timestamp_local": _hora_sp(),
                "message_id": f"acesso-dia-{wa_to}-{today}"
            })
            ACESSOS_DIA[wa_to] = today
        except Exception as e:
            print("[ACESSO DIA] erro:", e)

    # ==========================================================
    # 🔥 BOTÃO DE TEMPLATE (EX: clique em "Olá")
    # ==========================================================

    if mtype == "button":
        texto_btn = (msg.get("button", {}).get("text") or "").strip().lower()

        print("🔘 BOTÃO TEMPLATE RECEBIDO:", texto_btn)

        # Todos os botões de template abrem o menu inicial
        if texto_btn in {"olá", "ola", "agendar consulta", "falar com atendente"}:
            reset_sessao(wa_to)

            SESS[wa_to] = {
                "route": "root",
                "stage": "",
                "data": {},
                "last_at": _now_sp()
            }

            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

    # ===== INTERACTIVE =======================================================
    if mtype == "interactive":
        inter    = msg.get("interactive", {})
        br       = inter.get("button_reply") or {}
        lr       = inter.get("list_reply") or {}
        bid_id   = (br.get("id") or lr.get("id") or "").strip()
        if not bid_id:
            _send_buttons(wa_to, _welcome_named(profile_name), BTN_ROOT)
            return

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

            # ===== CPF DESATIVADO =====
            # SESS[wa_to] = {"route":"retorno","stage":"cpf","data":{"tipo":"retorno"}}
            # _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:")

            # >>> NOVO
            SESS[wa_to] = {"route":"retorno","stage":"nome","data":{"tipo":"retorno"}}
            _send_text(wa_to, "Para prosseguir, informe o nome completo do paciente:")
            return

        
        if bid_id == "op_resultado":

            # ===== CPF DESATIVADO =====
            # SESS[wa_to] = {"route":"resultado","stage":"cpf","data":{"tipo":"resultado"}}
            # _send_text(wa_to, "Para prosseguir, informe o CPF do paciente:")

            # >>> NOVO
            SESS[wa_to] = {"route":"resultado","stage":"nome","data":{"tipo":"resultado"}}
            _send_text(wa_to, "Para prosseguir, informe o nome completo do paciente:")
            return
        
        if bid_id == "op_mais3":
            SESS[wa_to] = {"route":"mais3","stage":"","data":{}}
            _send_buttons(wa_to, "Mais opções:", BTN_MAIS_3); return
        if bid_id == "op_endereco":
            # LOG leve do clique em Endereço (quem e quando)
            try:
                    # >>> LOG DE ACESSO AO ENDEREÇO
                    # Registra no Sheets toda vez que alguém clica em "Endereço".
                    # Isso permite medir interesse passivo mesmo sem agendamento.
                    _post_webapp({
                        "tipo": "acesso_endereco",         # Coluna E
                        "especialidade": "endereco",       # Coluna D (campo oficial)
                        "contato": (wa_to or "").strip(),
                        "whatsapp_nome": (profile_name or "").strip(),
                        "timestamp_local": _hora_sp(),
                    })
            except Exception as e:
                print("[LOG ENDERECO] aviso:", e)

            txt = (
                "📍 *Endereço*\n"
                "Rua Utrecht, 129 – Vila Rio Branco – CEP 03878-000 – São Paulo/SP\n\n"
                f"🌐 *Site*: {LINK_SITE}\n"
                f"📷 *Instagram*: {LINK_INSTAGRAM}\n"
                "📘 *Facebook*: Clinica Luma\n"
                "☎️ *Telefone*: (11) 2043-9937\n"
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

        # ===== BLOCO CPF/RG DO PACIENTE DESATIVADO TEMPORARIAMENTE =====
# Não estamos mais coletando documento do paciente.
# Mantido aqui apenas para possível reativação futura.

# if bid_id in {"pacdoc_sim","pacdoc_nao"}:
#     ses = SESS.get(wa_to) or {"route":"consulta","stage":"forma","data":{"tipo":"consulta"}}
#     if bid_id == "pacdoc_sim":
#         ses["stage"] = "paciente_doc"
#         SESS[wa_to] = ses
#         _send_text(wa_to, "Informe o CPF ou RG do paciente:")
#         return
#     else:
#         ses["data"]["paciente_documento"] = "Não possui"
#         ses["stage"] = None
#         SESS[wa_to] = ses
#         _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
#         return


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
            ses["data"]["_compl_decidido"] = True          # <--- NOVO
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
        if low in {"menu", "inicio", "início", "reiniciar", "start", "começar",
                    "ola", "olá", "oi", "bom dia", "boa tarde", "boa noite"}:

                    reset_sessao(wa_to)

                    SESS[wa_to] = {
                        "route": "root",
                        "stage": "",
                        "data": {},
                        "last_at": _now_sp()
                    }

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
            if op == 4:  # Panfletos
                ses["data"]["origem_cliente"] = "Panfleto"
                ses["stage"] = "origem_panfleto_codigo"; SESS[wa_to] = ses
                _send_text(wa_to, "P= ")  # apenas isso, aguardando o código
                return
            if op == 5:  # Outros (aberto)
                ses["data"]["origem_cliente"] = "Outros"   # <<< P = "Outros"
                ses["data"]["origem_outro_texto"] = ""     # <<< limpa R
                ses["stage"] = "origem_outros_texto"; SESS[wa_to] = ses
                _send_text(wa_to, "Pode nos dizer em poucas palavras de onde nos conheceu?"); return
            _send_text(wa_to, "Opção inválida. Escolha um número entre 0 e 5.")
            _send_text(wa_to, _origem_menu_texto()); return

        if ses and ses.get("stage") == "origem_outros_texto":
            texto = (body or "").strip()
            ses["data"]["origem_cliente"] = "Outros"             # <<< P
            ses["data"]["origem_outro_texto"] = texto            # <<< R
            ses["data"]["_origem_done"] = True
            ses["stage"] = None; SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses); return

        if ses and ses.get("stage") == "origem_panfleto_codigo":
            code_norm, code_raw = _normalize_panfleto(body)
            if not code_norm:
                _send_text(wa_to, "Código inválido. Responda com os números ou com P= seguido do código.")
                _send_text(wa_to, "P= ")
                return
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

    # ===== ENDEREÇO AUTOMÁTICO DESATIVADO TEMPORARIAMENTE =====
# if route in {"consulta","exames","editar_endereco"}:
#     if data.get("cep") and data.get("numero") and ("complemento" in data) and not data.get("endereco"):
#         end = _montar_endereco_via_cep(data["cep"], data["numero"], data.get("complemento",""))
#         if end: data["endereco"] = end
#         else:
#             ses["stage"] = "cep"
#             SESS[wa_to] = ses
#             _send_text(wa_to, "Não localizei o CEP. Envie 8 dígitos ou informe o endereço completo.")
#             return

    # Bifurcação paciente após escolha de forma+especialidade/exame
    if route == "consulta" and data.get("forma") and data.get("especialidade") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True; ses["stage"] = "paciente_escolha"; SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE); return
    if route == "exames" and data.get("forma") and data.get("exame") and not data.get("_pac_decidido"):
        data["_pac_decidido"] = True; ses["stage"] = "paciente_escolha"; SESS[wa_to] = ses
        _send_buttons(wa_to, "O atendimento é para você mesmo(a) ou para outro paciente (filho/dependente)?", BTN_PACIENTE); return

    fields = _fields_for(route, data) or []
    pend   = [(k, q) for (k, q) in fields if not data.get(k)]

    # ===== MARKETING ANTES DO CONFIRMAR =====================================
    # IMPORTANTE:
    # A coleta da origem (Instagram, Google, Panfleto, etc.)
    # ocorre ANTES da confirmação final.
    # Isso garante que:
    # 1) Sempre teremos P/Q/R preenchidos antes do salvamento
    # 2) O resumo final já mostre a origem
    # NÃO mover este bloco para depois do confirmar.
    # Quando já temos CEP+Número e a decisão sobre complemento (complemento presente,
    # mesmo que vazio), perguntamos a ORIGEM uma única vez, antes de montar o resumo.
    if route in {"consulta","exames"} and not data.get("_origem_done"):
        if data.get("cep") and data.get("numero"):
            ses["stage"] = "origem_menu"; SESS[wa_to] = ses
            _send_text(wa_to, _origem_menu_texto()); return

    # Quando todos os campos obrigatórios estão ok e marketing já foi coletado,
    # montamos a caixa de confirmação.
    if not pend and route in {"consulta","exames"} and not data.get("_confirmado"):
        # Se ainda não perguntamos marketing por algum motivo, faz agora.
        if not data.get("_origem_done"):
            ses["stage"] = "origem_menu"; SESS[wa_to] = ses
            _send_text(wa_to, _origem_menu_texto()); return

        resumo = [
            f"Responsável: {data.get('nome','')}",
            f"CPF: {data.get('cpf','')}  Nascimento: {data.get('nasc','')}",
            f"Forma: {data.get('forma','')}  Convênio: {data.get('convenio','') or '-'}",
        ]
        if data.get("_pac_outro"):
            resumo += [f"Paciente: {data.get('paciente_nome','')}  Nasc: {data.get('paciente_nasc','')}  Doc: {data.get('paciente_documento','') or '-'}"]
        if route=="consulta": resumo.append(f"Especialidade: {data.get('especialidade','')}")
        if route=="exames":   resumo.append(f"Exame: {data.get('exame','')}")
        # Origem/Marketing no resumo
        if data.get("panfleto_codigo"):
            resumo.append(f"Origem: Panfleto ({data.get('panfleto_codigo')})")
        elif data.get("origem_cliente"):
            resumo.append(f"Origem: {data.get('origem_cliente')}")
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

    # (REMOVIDO GANCHO ANTIGO) marketing depois do confirmar

    # Salvar e encerrar
    # >>> SALVAMENTO FINAL
    # Ordem correta:
    # 1) Atualiza paciente
    # 2) Registra solicitação no Sheets
    # 3) Envia mensagem de fechamento
    # NÃO inverter a ordem.
    _upsert_paciente(ss, data)
    _add_solicitacao(ss, data)

    # ==========================================================
    # ENCERRAMENTO OPERACIONAL PADRÃO (SEM TEMPLATE AUTOMÁTICO)
    # ==========================================================

    try:
        _send_text(
            wa_to,
            FECHAMENTO.get(route, "Solicitação registrada.")
        )
    except Exception as e:
        print("[FINALIZAÇÃO] erro ao enviar mensagem final:", e)

    # Reset sessão
    SESS[wa_to] = {"route":"root", "stage":"", "data":{}}

# ===== Continue form ==========================================================
def _continue_form(ss, wa_to, ses, user_text):
    route = ses["route"]; stage = ses.get("stage","" ); data  = ses["data"]

    # Reabrir UI correta se aguardando
    if (route == "consulta" and stage == "especialidade"): _ask_especialidade_num(wa_to, ses); return
    if (route == "exames" and stage == "exame_num"):       _ask_exame_num(wa_to, ses); return

    # Campo atual
    if stage:
        if stage in {"nasc", "cep"}: user_text = _normalize(stage, user_text)
        if stage == "forma": data["forma"] = _normalize("forma", user_text)
        else:
            # casos especiais (marketing) já tratados fora
            if stage not in {"origem_outros_texto", "origem_panfleto_codigo", "origem_menu", "exame_num"}:
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

            # ===== DESATIVADO TEMPORARIAMENTE =====
            # Não vamos mais pedir nascimento nem documento do paciente
            # Para reativar no futuro, basta remover o comentário abaixo

            # ses["stage"] = "paciente_nasc"
            # SESS[wa_to] = ses
            # _send_text(wa_to, "Data de nascimento do paciente (dd/mm/aaaa):")
            # return

            # >>> Agora seguimos direto
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        # ===== BLOCO DOCUMENTO PACIENTE DESATIVADO TEMPORARIAMENTE =====
        # if stage == "paciente_doc":
        #     data["paciente_documento"] = (user_text or "").strip()
        #     ses["stage"] = None
        #     SESS[wa_to] = ses
        #     _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
        #     return

    # Endereço
    if route in {"consulta","exames","editar_endereco"} and stage == "numero":
        if not data.get("numero"):
            _send_text(wa_to, "Informe o número (ou S/N):"); return
        ses["stage"] = "complemento_decisao"; SESS[wa_to] = ses
        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO); return

    if stage == "complemento_decisao":
        # Se já veio do botão "Sim", não repete a pergunta
        if data.get("_compl_decidido"):
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return

        l = (user_text or "").strip().lower()
        if l in {"nao", "não", "n", "no"}:
            data["complemento"] = ""
            ses["stage"] = None
            SESS[wa_to] = ses
            _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
            return

        if l in {"sim", "s", "yes", "y"}:
            ses["stage"] = "complemento"
            SESS[wa_to] = ses
            _send_text(wa_to, "Digite o complemento (apto, bloco, sala):")
            return

        _send_buttons(wa_to, "Possui complemento (apto, bloco, sala)?", BTN_COMPLEMENTO)
        return

    if stage == "complemento":
        data["complemento"] = (user_text or "").strip()
        # opcional: limpar o flag para evitar efeitos colaterais
        data.pop("_compl_decidido", None)
        # Remove flag interna para evitar reentrada em loop
        # Mantém o fluxo limpo após definir complemento
        ses["stage"] = None
        SESS[wa_to] = ses
        _finaliza_ou_pergunta_proximo(ss, wa_to, ses)
        return

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
