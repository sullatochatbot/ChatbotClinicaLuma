# responder.py — Clínica Luma (melhorado e corrigido)
from __future__ import annotations
import os, re, json, requests, time
from datetime import datetime, timezone, timedelta

# ===== Env/const =====
ACCESS_TOKEN    = os.getenv("ACCESS_TOKEN","").strip()
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID","").strip()
VERIFY_TOKEN    = os.getenv("VERIFY_TOKEN","clinica_luma_token")

PLANILHA_ID     = os.getenv("PLANILHA_ID","").strip()
GS_CRED_PATH    = os.getenv("GOOGLE_SHEET_JSON","credenciais_sheets.json").strip()

NOME_EMPRESA    = os.getenv("NOME_EMPRESA","Clínica Médica Luma")
LINK_SITE       = os.getenv("LINK_SITE","https://www.lumaclinicadafamilia.com.br")
LINK_INSTAGRAM  = os.getenv("LINK_INSTAGRAM","https://www.instagram.com/luma_clinicamedica")

GRAPH_BASE       = "https://graph.facebook.com/v20.0"
WHATSAPP_API_URL = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages" if PHONE_NUMBER_ID else ""
HEADERS          = {"Authorization": f"Bearer {ACCESS_TOKEN}" if ACCESS_TOKEN else "", "Content-Type":"application/json"}

# ===== Timezone SP =====
TZ_BR = timezone(timedelta(hours=-3))
def _tz_now_str() -> str:
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# ===== Google Sheets (opcional) =====
_gs_client=_gs_pagina1=_gs_historico=None
def _gs_ensure_headers(ws, headers):
    try: row1 = ws.row_values(1)
    except: row1 = []
    if row1 != headers:
        ws.resize(rows=max(getattr(ws,"row_count",1000),1000), cols=len(headers))
        ws.update(f"A1:{chr(64+len(headers))}1", [headers])

def _gs_try_init():
    global _gs_client,_gs_pagina1,_gs_historico
    if _gs_client is not None: return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        if not PLANILHA_ID or not os.path.exists(GS_CRED_PATH):
            print("[GS] Planilha/credenciais ausentes; logs não serão gravados.")
            _gs_client=False; return
        scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        creds=Credentials.from_service_account_file(GS_CRED_PATH, scopes=scopes)
        gc=gspread.authorize(creds); sh=gc.open_by_key(PLANILHA_ID)
        try: ws1=sh.worksheet("Pagina1")
        except: ws1=sh.add_worksheet(title="Pagina1", rows=1000, cols=10)
        _gs_ensure_headers(ws1, ["Numero","Nome","UltimoInteresse","AtualizadoEm","Especialidade"])
        try: wsh=sh.worksheet("Historico")
        except: wsh=sh.add_worksheet(title="Historico", rows=2000, cols=12)
        _gs_ensure_headers(wsh, ["DataHora","Numero","Nome","Evento","Detalhe","Origem","Especialidade"])
        _gs_client, _gs_pagina1, _gs_historico = gc, ws1, wsh
        print("[GS] Conectado.")
    except Exception as e:
        print("[GS] Falha ao iniciar:", e); _gs_client=False

def _gs_upsert_contato(numero, nome=None, interesse=None, especialidade=None):
    _gs_try_init()
    if not _gs_client: return
    try:
        ws=_gs_pagina1; cells=ws.col_values(1); numero=numero.strip(); idx=None
        for i,val in enumerate(cells, start=1):
            if i==1: continue
            if (val or "").strip()==numero: idx=i; break
        agora=_tz_now_str()
        if idx:
            if nome: ws.update_cell(idx,2,nome)
            if interesse: ws.update_cell(idx,3,interesse)
            ws.update_cell(idx,4,agora)
            if especialidade: ws.update_cell(idx,5,especialidade)
        else:
            ws.append_row([numero, nome or "", interesse or "", agora, especialidade or ""], value_input_option="USER_ENTERED")
    except Exception as e:
        print("[GS] upsert erro:", e)

def _gs_log(numero, nome, evento, detalhe="", origem="chatbot", especialidade=None):
    _gs_try_init()
    if not _gs_client: return
    try:
        _gs_historico.append_row([_tz_now_str(), numero, nome or "", evento, detalhe, origem, especialidade or ""], value_input_option="USER_ENTERED")
    except Exception as e:
        print("[GS] log erro:", e)

# ===== WhatsApp helpers =====
def _tem_credenciais(): return bool(ACCESS_TOKEN and PHONE_NUMBER_ID)

def _post_wa(payload, timeout=30):
    if not _tem_credenciais():
        print("[MOCK] WA:", json.dumps(payload, ensure_ascii=False)); return {"mock": True}
    r = requests.post(WHATSAPP_API_URL, headers=HEADERS, json=payload, timeout=timeout)
    if not (200 <= r.status_code < 300): print("[WA ERROR]", r.status_code, r.text)
    try: return r.json()
    except: return {"status_code": r.status_code, "text": r.text}

def enviar_texto(para, texto):
    return _post_wa({"messaging_product":"whatsapp","to":para,"type":"text","text":{"preview_url":False,"body":texto[:4096]}})

def enviar_botoes(para, corpo, botoes):
    interactive = {
        "type":"button",
        "body":{"text":corpo[:1024]},
        "action":{"buttons":[{"type":"reply","reply":{"id":b["id"],"title":b["titulo"][:20]}} for b in botoes[:3]]}
    }
    return _post_wa({"messaging_product":"whatsapp","to":para,"type":"interactive","interactive":interactive})

def enviar_typing(para, on=True):
    payload = {"messaging_product":"whatsapp","to":para,"type":"typing","typing":{"status":"on" if on else "off"}}
    return _post_wa(payload)

# ===== Menus / textos =====
MENU_INICIAL_BTNS = [
    {"id":"cons","titulo":"Agendar consulta"},
    {"id":"atd","titulo":"Falar com atendente"},
    {"id":"mais","titulo":"Informações gerais"},
]
MENU_INFO_BTNS = [
    {"id":"info_endereco","titulo":"Endereço"},
    {"id":"info_convenio","titulo":"Convênios"},
    {"id":"info_horario","titulo":"Horários"},
]
INFO_ENDERECO = (
    "Clínica Médica Luma\n"
    "Rua Utrecht, 129 – Ponte Rasa – CEP 03878-000 – São Paulo/SP\n"
    "Telefone/WhatsApp: (11) 96850-1810\n"
    "Site: https://www.lumaclinicadafamilia.com.br\n"
    "Instagram: @luma_clinicamedica\n"
    "Facebook: Clinica Luma"
)

# ===== Business hours + debounce + saudação controlada =====
def esta_no_horario():
    a = datetime.now(TZ_BR)
    if a.weekday() < 5:  # seg–sex
        return 8 <= a.hour < 18
    if a.weekday() == 5: # sábado
        return 8 <= a.hour < 12
    return False

ULTIMA_MSG_USUARIO = {}  # numero -> ts
def debounce_usuario(numero, janela_seg=5):
    agora = datetime.now(TZ_BR).timestamp()
    t_ant = ULTIMA_MSG_USUARIO.get(numero, 0)
    ULTIMA_MSG_USUARIO[numero] = agora
    return (agora - t_ant) < janela_seg  # True = ignora

ULTIMA_SAUDACAO = {}  # numero -> ts
def deve_saudacao(numero: str, janela_seg: int = 60) -> bool:
    agora = time.time()
    ultimo = ULTIMA_SAUDACAO.get(numero, 0)
    if (agora - ultimo) > janela_seg:
        ULTIMA_SAUDACAO[numero] = agora
        return True
    return False

# ===== Fluxos =====
def boas_vindas(numero, nome=None):
    nome_tpl = nome or "Cliente"
    enviar_typing(numero, True)
    if not esta_no_horario():
        enviar_texto(numero, f"Olá, {nome_tpl}! Nosso horário: Seg–Sex 08:00–18:00, Sáb 08:00–12:00.")
        enviar_botoes(numero, "Posso te encaminhar para um atendente ou mostrar informações gerais?", [
            {"id":"atd","titulo":"Falar com atendente"},
            {"id":"mais","titulo":"Informações gerais"}
        ])
        _gs_log(numero, nome, "menu_botoes", "FORA_HORARIO")
        enviar_typing(numero, False)
        return

    enviar_texto(numero, f"Olá, {nome_tpl}! Você está em contato com a {NOME_EMPRESA}. "
                         f"Para agilizar seu atendimento, escolha uma opção abaixo.")
    enviar_botoes(numero, "Escolha uma opção:", MENU_INICIAL_BTNS)
    _gs_log(numero, nome, "menu_botoes", "MENU_INICIAL")
    enviar_typing(numero, False)

def enviar_menu_informacoes(numero, nome=None):
    enviar_botoes(numero, "Informações gerais:", MENU_INFO_BTNS)
    _gs_log(numero, nome, "menu_botoes", "INFO_GERAIS")

def atender_humano(numero, nome=None):
    enviar_texto(numero, "Certo! Vou te encaminhar para um atendente humano. Se puder, envie um resumo breve. 🙏")
    _gs_log(numero, nome, "roteamento", "humano")

def iniciar_pre_agendamento(numero, nome=None):
    _gs_upsert_contato(numero, nome=nome, interesse="consulta")
    enviar_texto(numero, "Perfeito! Para agendarmos, informe por favor:\n"
                         "• Nome completo\n• Especialidade (ex.: Clínica Geral, Pediatria...)\n• Preferência de dia/horário")
    _gs_log(numero, nome, "pre_agendamento", "coletar_dados")

# ===== Interpretação =====
_RE_NOME = re.compile(r"(?:meu\s+nome\s+é|meu\s+nome\s*:?\s*|sou\s+|chamo-me\s+|eu\s+me\s+chamo\s+)(?P<nome>.+)$", re.IGNORECASE)
def extrair_nome_de_texto(txt):
    m = _RE_NOME.search((txt or "").strip())
    if not m: return None
    nm = re.sub(r"[\u2600-\u27BF\U0001F300-\U0001FAFF]+","", m.group("nome").strip())
    return nm[:60] if nm else None

_SPECIALTIES = ["clínica geral","clinica geral","pediatria","dermatologia","cardiologia","ginecologia","ortopedia",
                "oftalmologia","odontologia","psicologia","otorrinolaringologia","endocrinologia","urologia",
                "neurologia","nutrição","nutricao","fisioterapia"]
def extrair_especialidade(txt):
    t=(txt or "").lower().strip()
    m=re.search(r"(?:especialidade\s*:?\s*|consulta\s+em\s+|quero\s+)([a-zçãõéêíóúà ]{4,})", t)
    cand = m.group(1).strip() if m else t
    for esp in _SPECIALTIES:
        if esp in cand:
            return esp.title().replace("Clinica","Clínica").replace("Nutricao","Nutrição")
    return None

def processar_botao(numero, bid_ou_titulo, nome=None):
    if bid_ou_titulo in ("Agendar consulta","Falar com atendente","Informações gerais"):
        bid = {"Agendar consulta":"cons","Falar com atendente":"atd","Informações gerais":"mais"}.get(bid_ou_titulo, bid_ou_titulo)
    else:
        bid = bid_ou_titulo
    _gs_log(numero, nome, "click_botao", bid)

    if bid=="cons": iniciar_pre_agendamento(numero, nome); return
    if bid=="atd":  atender_humano(numero, nome);         return
    if bid=="mais": enviar_menu_informacoes(numero, nome); return

    if bid=="info_endereco": enviar_texto(numero, INFO_ENDERECO); return
    if bid=="info_convenio": enviar_texto(numero, "Convênios e pagamentos:\n• Convênios: consultar recepção\n• Particulares: PIX / Cartão / Boleto"); return
    if bid=="info_horario":  enviar_texto(numero, "Horários:\n• Seg–Sex: 08:00–18:00\n• Sábados: 08:00–12:00\n• Dom./Feriados: Plantão sob disponibilidade"); return

    if bid in ("1","2","3"): return processar_texto(numero, bid, nome_atual=nome)

    enviar_texto(numero, "Não entendi. Vou te mostrar opções novamente.")
    enviar_menu_informacoes(numero, nome)

def processar_texto(numero, texto, nome_atual=None):
    tnorm = (texto or "").strip()
    low    = tnorm.lower()

    # Opt-out simples
    if any(k in low for k in ["parar","descadastrar","remover","sair da lista"]):
        enviar_texto(numero, "Entendido. Você não receberá mais mensagens automáticas desta conversa. Se quiser retomar, é só dizer 'oi'.")
        _gs_log(numero, nome_atual, "optout", tnorm); return

    # Nome
    novo_nome = extrair_nome_de_texto(tnorm)
    if novo_nome:
        _gs_upsert_contato(numero, nome=novo_nome)
        _gs_log(numero, novo_nome, "nome_atualizado", novo_nome)
        enviar_texto(numero, f"Obrigado, {novo_nome}! Nome atualizado. 😊")
        boas_vindas(numero, novo_nome); return

    # Especialidade
    esp = extrair_especialidade(tnorm)
    if esp:
        _gs_upsert_contato(numero, nome=nome_atual, especialidade=esp)
        _gs_log(numero, nome_atual, "especialidade", esp, especialidade=esp)
        enviar_texto(numero, f"Anotado: especialidade = {esp}. Qual dia/horário prefere?")
        return

    # Saudações — NÃO repete menu (já enviado pelo entry)
    if low in {"oi","olá","ola","bom dia","boa tarde","boa noite","hello","hi","menu"}:
        return

    # Atalhos
    if any(k in low for k in ["endereco","endereço","site","contato","telefone"]):
        enviar_texto(numero, INFO_ENDERECO); return
    if "agend" in low:
        iniciar_pre_agendamento(numero, nome_atual); return
    if any(k in low for k in ["humano","atendente","falar com atendente","pessoa"]):
        atender_humano(numero, nome_atual); return

    # '1/2/3'
    if low == "1": enviar_texto(numero, INFO_ENDERECO); return
    if low == "2": enviar_texto(numero, "Convênios e pagamentos:\n• Convênios: consultar recepção\n• Particulares: PIX / Cartão / Boleto"); return
    if low == "3": enviar_texto(numero, "Horários:\n• Seg–Sex: 08:00–18:00\n• Sábados: 08:00–12:00\n• Dom./Feriados: Plantão sob disponibilidade"); return

    # Fallback sem repetir menu
    enviar_texto(numero, f"Não entendi sua mensagem, {nome_atual or 'Cliente'}. Escolha uma opção acima 👆")

# ===== Entrada principal =====
def responder_evento_mensagem(entry: dict) -> None:
    try:
        changes = entry.get("changes", [])
        if not changes: return
        value = changes[0].get("value", {})
        msgs  = value.get("messages", [])
        if not msgs: return
        msg   = msgs[0]
        numero= msg.get("from")

        # Debounce 5s
        if debounce_usuario(numero):
            print("[FLOW] Ignorado por debounce:", numero); return

        contato = (value.get("contacts") or [{}])[0]
        perfil  = contato.get("profile", {}) if isinstance(contato, dict) else {}
        nome    = perfil.get("name")

        _gs_upsert_contato(numero, nome=nome)
        _gs_log(numero, nome, "acesso", msg.get("type",""))

        # 👉 NÃO saudar em cliques de botão
        if msg.get("type") == "interactive":
            inter = msg.get("interactive", {})
            if inter.get("type") == "button_reply":
                rep = inter.get("button_reply", {}); bid = rep.get("id") or rep.get("title")
                if bid: processar_botao(numero, bid, nome); return
            if inter.get("type") == "list_reply":
                rep = inter.get("list_reply", {}); opt = rep.get("id") or rep.get("title")
                if opt: processar_botao(numero, opt, nome); return
            return

        # 👉 Texto: saudar só se ainda não saudou nessa janela (60s)
        if msg.get("type") == "text":
            body = msg.get("text", {}).get("body", "")
            if deve_saudacao(numero):
                boas_vindas(numero, nome)
            processar_texto(numero, body, nome)
            return

    except Exception as e:
        print("[responder_evento_mensagem] erro:", e)

if __name__ == "__main__":
    to = os.environ.get("TEST_TO", "5511999999999")
    boas_vindas(to, "Cliente Teste")
    processar_texto(to, "oi", "Cliente Teste")
