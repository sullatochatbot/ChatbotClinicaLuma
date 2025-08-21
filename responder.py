# responder.py — Clínica Luma (refatorado e comentado)
# ----------------------------------------------------
# Objetivo:
# - Em QUALQUER mensagem recebida, enviar SAUDAÇÃO com NOME + MENU inicial (botões)
# - Em seguida, processar o texto/botões para continuar o fluxo
#
# Principais funções:
# - responder_evento_mensagem(entry): ponto de entrada chamado pelo webhook.py
# - boas_vindas(numero, nome): envia texto de boas-vindas e os botões do menu inicial
# - processar_texto(...): interpreta mensagens livres (nome, especialidade, atalhos)
# - processar_botao(...): roteia cliques dos botões
#
# Observação:
# - Google Sheets é opcional; se PLANILHA_ID/credenciais não estiverem setados, apenas logamos no console.

from __future__ import annotations

import os
import re
import json
import typing as t
from datetime import datetime, timezone, timedelta
import requests

# =========================
# Credenciais e Constantes
# =========================
ACCESS_TOKEN   = os.getenv("ACCESS_TOKEN", "").strip()
PHONE_NUMBER_ID= os.getenv("PHONE_NUMBER_ID", "").strip()
VERIFY_TOKEN   = os.getenv("VERIFY_TOKEN", "clinica_luma_token")

PLANILHA_ID    = os.getenv("PLANILHA_ID", "").strip()  # ID da planilha Google
GS_CRED_PATH   = os.getenv("GOOGLE_SHEET_JSON", "credenciais_sheets.json").strip()

NOME_EMPRESA   = os.getenv("NOME_EMPRESA", "Clínica Médica Luma")
LINK_SITE      = os.getenv("LINK_SITE", "https://www.lumaclinicadafamilia.com.br")
LINK_INSTAGRAM = os.getenv("LINK_INSTAGRAM", "https://www.instagram.com/luma_clinicamedica")

# (opcional) Templates — mantidos para uso futuro
TPL_BOAS_VINDAS   = "boas_vindas_clinica_luma"
TPL_CONFIRMACAO   = "confirmacao_consulta"
TPL_LEMBRETE_24H  = "lembrete_consulta_24h"
TPL_RESULTADO     = "resultado_exame_disponivel"
TPL_POS_ATEND     = "pos_atendimento_orientacoes"
TPL_REABERTURA    = "reabertura_contato_servico"

GRAPH_BASE        = "https://graph.facebook.com/v20.0"
WHATSAPP_API_URL  = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages" if PHONE_NUMBER_ID else ""
HEADERS           = {"Authorization": f"Bearer {ACCESS_TOKEN}" if ACCESS_TOKEN else "", "Content-Type": "application/json"}

# =========================
# Timezone Brasil (sem DST)
# =========================
TZ_BR = timezone(timedelta(hours=-3))  # Brasília
def _tz_now_str() -> str:
    """Retorna a hora atual em São Paulo (UTC-3)"""
    return datetime.now(TZ_BR).strftime("%Y-%m-%d %H:%M:%S -03:00")

# =========================
# Google Sheets (opcional)
# =========================
_gs_client = None
_gs_pagina1 = None
_gs_historico = None

def _gs_ensure_headers(ws, headers: list[str]):
    try:
        row1 = ws.row_values(1)
    except Exception:
        row1 = []
    if row1 != headers:
        ws.resize(rows=max(getattr(ws, "row_count", 1000), 1000), cols=len(headers))
        ws.update(f"A1:{chr(64+len(headers))}1", [headers])

def _gs_try_init():
    """Inicializa cliente e abas. Se faltar credencial, apenas loga e segue."""
    global _gs_client, _gs_pagina1, _gs_historico
    if _gs_client is not None:
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        if not PLANILHA_ID or not os.path.exists(GS_CRED_PATH):
            print("[GS] Planilha ou credenciais ausentes. Logs não serão gravados.")
            _gs_client = False
            return

        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(GS_CRED_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(PLANILHA_ID)

        try:
            ws1 = sh.worksheet("Pagina1")  # Nome simples p/ evitar acento
        except Exception:
            ws1 = sh.add_worksheet(title="Pagina1", rows=1000, cols=10)
        _gs_ensure_headers(ws1, ["Numero", "Nome", "UltimoInteresse", "AtualizadoEm", "Especialidade"])

        try:
            wsh = sh.worksheet("Historico")
        except Exception:
            wsh = sh.add_worksheet(title="Historico", rows=2000, cols=12)
        _gs_ensure_headers(wsh, ["DataHora", "Numero", "Nome", "Evento", "Detalhe", "Origem", "Especialidade"])

        _gs_client   = gc
        _gs_pagina1  = ws1
        _gs_historico= wsh
        print("[GS] Conectado e abas prontas.")
    except Exception as e:
        print("[GS] Falha ao iniciar:", e)
        _gs_client = False

def _gs_upsert_contato(numero: str, nome: str | None = None, interesse: str | None = None, especialidade: str | None = None):
    """Cria/atualiza contato em Pagina1 pelo número."""
    _gs_try_init()
    if not _gs_client:
        return
    try:
        ws = _gs_pagina1
        cells = ws.col_values(1)  # Numero
        numero = numero.strip()
        idx = None
        for i, val in enumerate(cells, start=1):
            if i == 1:  # header
                continue
            if (val or "").strip() == numero:
                idx = i
                break

        agora = _tz_now_str()
        if idx:
            if nome is not None and nome != "":
                ws.update_cell(idx, 2, nome)
            if interesse:
                ws.update_cell(idx, 3, interesse)
            ws.update_cell(idx, 4, agora)
            if especialidade:
                ws.update_cell(idx, 5, especialidade)
        else:
            ws.append_row([numero, nome or "", interesse or "", agora, especialidade or ""], value_input_option="USER_ENTERED")
    except Exception as e:
        print("[GS] upsert erro:", e)

def _gs_log(numero: str, nome: str | None, evento: str, detalhe: str = "", origem: str = "chatbot", especialidade: str | None = None):
    """Registra interação na aba Historico."""
    _gs_try_init()
    if not _gs_client:
        return
    try:
        _gs_historico.append_row([_tz_now_str(), numero, nome or "", evento, detalhe, origem, especialidade or ""], value_input_option="USER_ENTERED")
    except Exception as e:
        print("[GS] log erro:", e)

# =========================
# Util WhatsApp (envio)
# =========================
def _tem_credenciais() -> bool:
    return bool(ACCESS_TOKEN and PHONE_NUMBER_ID)

def _post_wa(payload: dict, timeout: int = 30) -> dict:
    if not _tem_credenciais():
        print("[MOCK] Envio WhatsApp:", json.dumps(payload, ensure_ascii=False))
        return {"mock": True, "payload": payload}
    resp = requests.post(WHATSAPP_API_URL, headers=HEADERS, json=payload, timeout=timeout)
    if not (200 <= resp.status_code < 300):
        print("[WA ERROR]", resp.status_code, resp.text)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}

def enviar_texto(para: str, texto: str) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": para,
        "type": "text",
        "text": {"preview_url": False, "body": texto[:4096]},
    }
    return _post_wa(payload)

def enviar_botoes(para: str, corpo: str, botoes: list[dict]) -> dict:
    """
    botoes = [{"id": "cons", "titulo": "Agendar consulta"}, ...] — máx. 3
    """
    interactive = {
        "type": "button",
        "body": {"text": corpo[:1024]},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["titulo"][:20]}}
                for b in botoes[:3]
            ]
        },
    }
    payload = {"messaging_product": "whatsapp", "to": para, "type": "interactive", "interactive": interactive}
    return _post_wa(payload)

def enviar_template(para: str, nome_modelo: str, linguagem: str = "pt_BR", vars_corpo: list[str] | None = None, url_botao: str | None = None) -> dict:
    """Mantido para uso posterior (lembrete, confirmação etc.). Não usado na saudação inicial."""
    components: list[dict] = []
    if vars_corpo:
        components.append({"type": "body", "parameters": [{"type": "text", "text": v} for v in vars_corpo]})
    if url_botao:
        components.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": url_botao}]})
    template: dict = {"name": nome_modelo, "language": {"code": linguagem}}
    if components:
        template["components"] = components
    payload = {"messaging_product": "whatsapp", "to": para, "type": "template", "template": template}
    return _post_wa(payload)

# =========================
# Menus / Textos prontos
# =========================
MENU_INICIAL_BTNS = [
    {"id": "cons", "titulo": "Agendar consulta"},
    {"id": "atd",  "titulo": "Falar com atendente"},
    {"id": "mais", "titulo": "Informações gerais"},
]

MENU_INFO_BTNS = [
    {"id": "info_endereco", "titulo": "Endereço"},
    {"id": "info_convenio", "titulo": "Convênios"},
    {"id": "info_horario",  "titulo": "Horários"},
]

INFO_ENDERECO = (
    "Clínica Médica Luma\n"
    "Rua Utrecht, 129 – Ponte Rasa – CEP 03878-000 – São Paulo/SP\n"
    "Telefone/WhatsApp: (11) 96850-1810\n"
    "Site: https://www.lumaclinicadafamilia.com.br\n"
    "Instagram: @luma_clinicamedica\n"
    "Facebook: Clinica Luma"
)

# =========================
# Fluxos e helpers de negócio
# =========================
def boas_vindas(numero: str, nome: str | None = None):
    """
    SAUDAÇÃO PADRÃO (SEMPRE): envia texto com o nome + em seguida, botões do menu inicial.
    Obs.: não usa template aqui para garantir ordem (texto -> botões) no WhatsApp.
    """
    nome_tpl = (nome or "Cliente")
    # 1) Texto com nome
    enviar_texto(numero, f"Olá, {nome_tpl}! Você está em contato com a {NOME_EMPRESA}. "
                         f"Para agilizar seu atendimento, escolha uma opção abaixo.")
    # 2) Botões do menu inicial
    enviar_botoes(numero, "Escolha uma opção:", MENU_INICIAL_BTNS)
    _gs_log(numero, nome, "menu_botoes", "MENU_INICIAL")

def enviar_menu_informacoes(numero: str, nome: str | None = None):
    enviar_botoes(numero, "Informações gerais:", MENU_INFO_BTNS)
    _gs_log(numero, nome, "menu_botoes", "INFO_GERAIS")

def atender_humano(numero: str, nome: str | None = None):
    enviar_texto(numero, "Certo! Vou te encaminhar para um atendente humano. Se puder, envie um resumo da sua necessidade. 🙏")
    _gs_log(numero, nome, "roteamento", "humano")

def iniciar_pre_agendamento(numero: str, nome: str | None = None):
    _gs_upsert_contato(numero, nome=nome, interesse="consulta")
    enviar_texto(numero, "Perfeito! Para agendarmos, informe por favor:\n"
                         "• Nome completo\n"
                         "• Especialidade (ex.: Clínica Geral, Pediatria...)\n"
                         "• Preferência de dia/horário")
    _gs_log(numero, nome, "pre_agendamento", "coletar_dados")

# --- detecção simples de nome em frases do usuário ---
_RE_NOME = re.compile(r"(?:meu\s+nome\s+é|meu\s+nome\s*:?|sou\s+|chamo-me\s+|eu\s+me\s+chamo\s+)(?P<nome>.+)$", re.IGNORECASE)
def extrair_nome_de_texto(texto: str) -> str | None:
    m = _RE_NOME.search((texto or "").strip())
    if not m:
        return None
    nome = m.group("nome").strip()
    nome = re.sub(r"[\u2600-\u27BF\U0001F300-\U0001FAFF]+", "", nome).strip()  # remove emojis
    return nome[:60] if nome else None

_SPECIALTIES = [
    "clínica geral","clinica geral","pediatria","dermatologia","cardiologia","ginecologia","ortopedia",
    "oftalmologia","odontologia","psicologia","otorrinolaringologia","endocrinologia","urologia","neurologia",
    "nutrição","nutricao","fisioterapia"
]
def extrair_especialidade(texto: str) -> str | None:
    t = (texto or "").lower().strip()
    m = re.search(r"(?:especialidade\s*:?\s*|consulta\s+em\s+|quero\s+)([a-zçãõéêíóúà ]{4,})", t)
    candidato = m.group(1).strip() if m else t
    for esp in _SPECIALTIES:
        if esp in candidato:
            return esp.title().replace("Clinica", "Clínica").replace("Nutricao", "Nutrição")
    return None

def processar_botao(numero: str, button_id_ou_titulo: str, nome: str | None = None):
    """Mapeia títulos exibidos pelo WhatsApp para IDs locais e roteia."""
    if button_id_ou_titulo in ("Agendar consulta", "Falar com atendente", "Informações gerais"):
        mapa = {"Agendar consulta": "cons", "Falar com atendente": "atd", "Informações gerais": "mais"}
        button_id = mapa.get(button_id_ou_titulo, button_id_ou_titulo)
    else:
        button_id = button_id_ou_titulo

    _gs_log(numero, nome, "click_botao", button_id)

    if button_id == "cons":
        iniciar_pre_agendamento(numero, nome);  return
    if button_id == "atd":
        atender_humano(numero, nome);          return
    if button_id == "mais":
        enviar_menu_informacoes(numero, nome); return

    # Submenus de informações gerais (botões)
    if button_id == "info_endereco":
        enviar_texto(numero, INFO_ENDERECO);   return
    if button_id == "info_convenio":
        enviar_texto(numero, "Convênios e pagamentos:\n• Convênios: (consultar disponibilidade em recepção)\n• Particulares: PIX / Cartão / Boleto"); return
    if button_id == "info_horario":
        enviar_texto(numero, "Horários:\n• Seg–Sex: 08:00–18:00\n• Sábados: 08:00–12:00\n• Dom./Feriados: Plantão sob disponibilidade"); return

    # Compatibilidade com quem digitar 1/2/3
    if button_id in ("1","2","3"):
        return processar_texto(numero, button_id, nome_atual=nome)

    enviar_texto(numero, "Não entendi. Vou te mostrar o menu novamente.")
    boas_vindas(numero, nome)

def processar_texto(numero: str, texto: str, nome_atual: str | None = None):
    """Regra para texto livre: detecta nome, especialidade e atalhos."""
    tnorm = (texto or "").strip()
    tnorm_low = tnorm.lower()

    # Nome declarado
    novo_nome = extrair_nome_de_texto(tnorm)
    if novo_nome:
        _gs_upsert_contato(numero, nome=novo_nome)
        _gs_log(numero, novo_nome, "nome_atualizado", novo_nome)
        enviar_texto(numero, f"Obrigado, {novo_nome}! Nome atualizado. 😊")
        boas_vindas(numero, novo_nome)
        return

    # Especialidade
    esp = extrair_especialidade(tnorm)
    if esp:
        _gs_upsert_contato(numero, nome=nome_atual, especialidade=esp)
        _gs_log(numero, nome_atual, "especialidade", esp, especialidade=esp)
        enviar_texto(numero, f"Anotado: especialidade pretendida = {esp}.")
        enviar_texto(numero, "Informe, por favor, a preferência de dia/horário para verificarmos a melhor agenda.")
        return

    # Saudações → menu (mantido)
    if tnorm_low in {"oi","olá","ola","bom dia","boa tarde","boa noite","hello","hi","menu"}:
        boas_vindas(numero, nome_atual); return

    # Compatibilidade com '1/2/3' (mesmo sem botões)
    if tnorm_low == "1":
        enviar_texto(numero, INFO_ENDERECO); return
    if tnorm_low == "2":
        enviar_texto(numero, "Convênios e pagamentos:\n• Convênios: (consultar disponibilidade em recepção)\n• Particulares: PIX / Cartão / Boleto"); return
    if tnorm_low == "3":
        enviar_texto(numero, "Horários:\n• Seg–Sex: 08:00–18:00\n• Sábados: 08:00–12:00\n• Dom./Feriados: Plantão sob disponibilidade"); return

    # Atalhos comuns
    if any(k in tnorm_low for k in ["endereco","endereço","site","contato","telefone"]):
        enviar_texto(numero, INFO_ENDERECO); return
    if "agend" in tnorm_low:
        iniciar_pre_agendamento(numero, nome_atual); return
    if any(k in tnorm_low for k in ["humano","atendente","falar com atendente","pessoa"]):
        atender_humano(numero, nome_atual); return

    # Fallback padrão → repete menu
    nome_base = nome_atual or "Cliente"
    enviar_texto(numero, f"Não entendi sua mensagem, {nome_base}. Posso te ajudar por aqui 👇")
    boas_vindas(numero, nome_atual)

# =========================
# Entrada pública (webhook)
# =========================
def responder_evento_mensagem(entry: dict) -> None:
    """
    Recebe um 'entry' do webhook (Meta):
    - Mensagens de texto
    - Cliques em botões (interactive/button_reply ou list_reply)
    - Salva/atualiza nome e registra logs no Google Sheets
    - NOVO: SEMPRE envia boas-vindas (texto + botões) ao receber qualquer mensagem
    """
    try:
        changes = entry.get("changes", [])
        if not changes:
            return
        value = changes[0].get("value", {})
        msgs = value.get("messages", [])
        if not msgs:
            return

        msg    = msgs[0]
        numero = msg.get("from")

        # Perfil (nome do contato)
        contato = (value.get("contacts") or [{}])[0]
        perfil  = contato.get("profile", {}) if isinstance(contato, dict) else {}
        nome    = perfil.get("name")

        # Cadastro + log de acesso
        _gs_upsert_contato(numero, nome=nome)
        _gs_log(numero, nome, "acesso", msg.get("type", ""))

        # 🔹 SEMPRE: boas-vindas logo de cara (garante ordem correta Texto -> Botões)
        boas_vindas(numero, nome)

        # Interativos
        if msg.get("type") == "interactive":
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                reply = interactive.get("button_reply", {})
                button_id = reply.get("id") or reply.get("title")
                if button_id:
                    processar_botao(numero, button_id, nome); return
            if interactive.get("type") == "list_reply":
                reply = interactive.get("list_reply", {})
                opt = reply.get("id") or reply.get("title")
                if opt:
                    processar_botao(numero, opt, nome); return

        # Texto
        if msg.get("type") == "text":
            texto = msg.get("text", {}).get("body", "")
            processar_texto(numero, texto, nome); return

        # Outros tipos → só mantém menu (já enviado acima)
        return

    except Exception as e:
        print("[responder_evento_mensagem] erro:", e)

# =========================
# Testes locais (opcional)
# =========================
if __name__ == "__main__":
    # Ex.: python responder.py 5511987654321 "oi"
    import sys
    to   = sys.argv[1] if len(sys.argv) > 1 else "5511999999999"
    body = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "oi"
    print(">> Teste local:", to, "|", body)
    boas_vindas(to, "Cliente Teste")
    processar_texto(to, body, nome_atual="Cliente Teste")
