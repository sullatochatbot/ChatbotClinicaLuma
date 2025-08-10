# responder.py — Clínica Luma (Fase 1)
# ------------------------------------------------------------
# Objetivo: Fluxo inicial para Clínica (Consulta, Exames, Mais),
# captação guiada de dados (nome, cpf, data de nascimento, convênio,
# especialidade, exame, etc.) com máquina de estados simples, botões
# interativos e logs em CSV + hooks para Google Sheets.
#
# Este arquivo foi pensado para ser "drop-in" no seu projeto atual,
# sem mudar webhook.py. Ele expõe a função `responder(evento)` que pode
# ser chamada pelo webhook ao receber mensagens. Se seu webhook chama
# outra função, ajuste no final conforme indicado.
# ------------------------------------------------------------

import os
import re
import json
import time
import csv
from datetime import datetime

import requests

# ------------------------------------------------------------
# Config .env
# ------------------------------------------------------------
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "sullato_token_verificacao")
PLANILHA_ID = os.getenv("PLANILHA_ID", "")
GOOGLE_SHEET_JSON = os.getenv("GOOGLE_SHEET_JSON", "credenciais_sheets.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ------------------------------------------------------------
# Constantes e utilitários
# ------------------------------------------------------------
WHATSAPP_API_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

CSV_PRIMEIRO = "PrimeiroAtendimento.csv"  # Upsert por número
CSV_HISTORICO = "Historico.csv"          # Append por evento

MENU_INICIAL_BTNS = [
    {"id": "cons", "title": "Consulta"},
    {"id": "exam", "title": "Exames"},
    {"id": "mais", "title": "Mais opções"},
]

CONVENIO_BTNS = [
    {"id": "cons_conv", "title": "Convênio"},
    {"id": "cons_part", "title": "Particular"},
]

PREFERENCIA_BTNS = [
    {"id": "pref_manha", "title": "Manhã"},
    {"id": "pref_tarde", "title": "Tarde"},
    {"id": "pref_qualq", "title": "Qualquer"},
]

ESPECIALIDADES_BTNS_P1 = [
    {"id": "esp_clinico", "title": "Clínico Geral"},
    {"id": "esp_pediatria", "title": "Pediatria"},
    {"id": "esp_gineco", "title": "Ginecologia"},
]
ESPECIALIDADES_BTNS_P2 = [
    {"id": "esp_cardio", "title": "Cardiologia"},
    {"id": "esp_orto", "title": "Ortopedia"},
    {"id": "esp_outro", "title": "Outra"},
]

EXAMES_BTNS_P1 = [
    {"id": "ex_hemo", "title": "Hemograma"},
    {"id": "ex_raiox", "title": "Raio-X"},
    {"id": "ex_ultra", "title": "Ultrassom"},
]
EXAMES_BTNS_P2 = [
    {"id": "ex_eletro", "title": "Eletro"},
    {"id": "ex_urina", "title": "Urina"},
    {"id": "ex_outro", "title": "Outro"},
]

SIM_NAO_BTNS = [
    {"id": "sim", "title": "Sim"},
    {"id": "nao", "title": "Não"},
]

# Máquina de estados em memória
ESTADOS = {}
# Estrutura por número: {
#   "etapa": str,
#   "dados": {"nome":..., "cpf":..., "nascimento":..., ...},
#   "tipo": "Consulta"|"Exame"|None,
#   "modalidade": "Convênio"|"Particular"|None
# }


def agora_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------
# Validações
# ------------------------------------------------------------
CPF_DIGITS = re.compile(r"\D+")
DATA_REGEX = re.compile(r"^(0?[1-9]|[12][0-9]|3[01])/(0?[1-9]|1[012])/(\d{4})$")


def normalizar_cpf(cpf: str) -> str:
    return CPF_DIGITS.sub("", cpf or "")


def cpf_valido(cpf: str) -> bool:
    d = normalizar_cpf(cpf)
    return len(d) == 11  # Fase 1: validação simples


def data_valida(data: str) -> bool:
    if not data:
        return False
    m = DATA_REGEX.match(data.strip())
    return m is not None


# ------------------------------------------------------------
# Envio de mensagens WhatsApp
# ------------------------------------------------------------

def enviar_texto(para: str, texto: str):
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("[WARN] ACCESS_TOKEN/PHONE_NUMBER_ID ausentes; simulação de envio:", texto)
        return {"mock": True}
    payload = {
        "messaging_product": "whatsapp",
        "to": para,
        "type": "text",
        "text": {"body": texto}
    }
    r = requests.post(WHATSAPP_API_URL, headers=HEADERS, json=payload, timeout=30)
    if r.status_code >= 400:
        print("[WA ERROR]", r.status_code, r.text)
    return r.json() if r.text else {}


def enviar_botoes(para: str, texto: str, botoes: list):
    """
    botoes: lista de {id, title}
    """
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("[WARN] ACCESS_TOKEN/PHONE_NUMBER_ID ausentes; simulação de botões:", texto, botoes)
        return {"mock": True}
    # WhatsApp Cloud API: interactive buttons (máx 3 por mensagem)
    # Como temos páginas, enviamos em blocos de 3.
    btns = [{
        "type": "reply",
        "reply": {"id": b["id"], "title": b["title"]}
    } for b in botoes[:3]]

    payload = {
        "messaging_product": "whatsapp",
        "to": para,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {"buttons": btns}
        }
    }
    r = requests.post(WHATSAPP_API_URL, headers=HEADERS, json=payload, timeout=30)
    if r.status_code >= 400:
        print("[WA ERROR]", r.status_code, r.text)
    return r.json() if r.text else {}


# ------------------------------------------------------------
# Persistência: CSV + Hooks para Google Sheets
# ------------------------------------------------------------
PRIMEIRO_COLS = [
    "timestamp_primeiro", "ultimo_timestamp", "numero_whatsapp",
    "nome", "cpf", "data_nascimento",
    "tipo", "modalidade", "convenio", "carteirinha",
    "especialidade", "exame", "pedido_medico", "preferencia_turno",
    "status", "observacoes"
]

HISTORICO_COLS = [
    "timestamp", "numero_whatsapp", "etapa", "acao", "valor", "contexto"
]


def _csv_ensure_headers(path: str, headers: list):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)


def _csv_read_all(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _csv_write_all(path: str, headers: list, rows: list):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def upsert_primeiro_atendimento(dados: dict):
    _csv_ensure_headers(CSV_PRIMEIRO, PRIMEIRO_COLS)
    rows = _csv_read_all(CSV_PRIMEIRO)
    numero = dados.get("numero_whatsapp")
    agora = agora_iso()

    # Monta registro padrão
    base = {c: "" for c in PRIMEIRO_COLS}
    base.update({
        "timestamp_primeiro": agora,
        "ultimo_timestamp": agora,
        "numero_whatsapp": numero,
    })

    # Procura existente por número
    idx = None
    for i, r in enumerate(rows):
        if r.get("numero_whatsapp") == numero:
            idx = i
            break

    if idx is None:
        # Novo registro
        for k, v in dados.items():
            if k in base and v is not None:
                base[k] = str(v)
        rows.append(base)
    else:
        # Atualiza registro existente (upsert)
        reg = rows[idx]
        reg["ultimo_timestamp"] = agora
        for k, v in dados.items():
            if k in reg and v is not None and str(v) != "":
                reg[k] = str(v)
        rows[idx] = reg

    _csv_write_all(CSV_PRIMEIRO, PRIMEIRO_COLS, rows)

    # Hook opcional: Google Sheets (implemente aqui chamando sua função existente)
    # try:
    #     salvar_em_google_sheets(PLANILHA_ID, "PrimeiroAtendimento", rows[-1])
    # except Exception as e:
    #     print("[Sheets] Falha upsert PrimeiroAtendimento:", e)


def log_historico(numero: str, etapa: str, acao: str, valor: str, contexto: dict | None = None):
    _csv_ensure_headers(CSV_HISTORICO, HISTORICO_COLS)
    row = {
        "timestamp": agora_iso(),
        "numero_whatsapp": numero,
        "etapa": etapa,
        "acao": acao,
        "valor": valor,
        "contexto": json.dumps(contexto or {}, ensure_ascii=False)
    }
    rows = _csv_read_all(CSV_HISTORICO)
    rows.append(row)
    _csv_write_all(CSV_HISTORICO, HISTORICO_COLS, rows)

    # Hook opcional: Google Sheets (implemente aqui chamando sua função existente)
    # try:
    #     salvar_em_google_sheets(PLANILHA_ID, "Historico", row)
    # except Exception as e:
    #     print("[Sheets] Falha append Historico:", e)


# ------------------------------------------------------------
# Máquina de estados: helpers
# ------------------------------------------------------------

def get_estado(numero: str) -> dict:
    return ESTADOS.get(numero, {"etapa": "inicio", "dados": {}, "tipo": None, "modalidade": None})


def set_estado(numero: str, estado: dict):
    ESTADOS[numero] = estado


def set_etapa(numero: str, etapa: str):
    est = get_estado(numero)
    est["etapa"] = etapa
    set_estado(numero, est)


def salvar_dado(numero: str, chave: str, valor):
    est = get_estado(numero)
    est["dados"][chave] = valor
    set_estado(numero, est)


def limpar_estado(numero: str):
    if numero in ESTADOS:
        del ESTADOS[numero]


# ------------------------------------------------------------
# Fluxo: mensagens e botões
# ------------------------------------------------------------

def boas_vindas(numero: str, nome: str | None = None):
    texto = (
        f"Olá{f' {nome}' if nome else ''}! 👋 Sou o atendimento virtual da Clínica Luma.\n"
        "Como posso te ajudar hoje?"
    )
    enviar_botoes(numero, texto, MENU_INICIAL_BTNS)


def perguntar_convenio_ou_particular(numero: str):
    enviar_botoes(numero, "Para sua consulta, você usará convênio ou será particular?", CONVENIO_BTNS)


def perguntar_dados_basicos(numero: str, incluir_convenio: bool):
    # Nome
    enviar_texto(numero, "Por favor, me informe o *nome completo* do paciente.")
    set_etapa(numero, "cons_nome")
    salvar_dado(numero, "coletar_convenio", incluir_convenio)


def perguntar_especialidade(numero: str):
    enviar_botoes(numero, "Qual especialidade você procura?", ESPECIALIDADES_BTNS_P1)
    time.sleep(0.6)
    enviar_botoes(numero, "Mais opções:", ESPECIALIDADES_BTNS_P2)


def perguntar_preferencia(numero: str):
    enviar_botoes(numero, "Qual sua preferência de atendimento?", PREFERENCIA_BTNS)


def resumo_confirmacao(numero: str):
    est = get_estado(numero)
    d = est.get("dados", {})
    linhas = [
        "Confira seus dados:",
        f"• Tipo: {est.get('tipo') or '—'}",
        f"• Modalidade: {est.get('modalidade') or '—'}",
        f"• Nome: {d.get('nome', '—')}",
        f"• CPF: {d.get('cpf', '—')}",
        f"• Nascimento: {d.get('nascimento', '—')}",
        f"• Convênio: {d.get('convenio', '—')}",
        f"• Carteirinha: {d.get('carteirinha', '—')}",
        f"• Especialidade: {d.get('especialidade', '—')}",
        f"• Exame: {d.get('exame', '—')}",
        f"• Pedido médico: {d.get('pedido_medico', '—')}",
        f"• Preferência: {d.get('preferencia', '—')}",
    ]
    enviar_texto(numero, "\n".join(linhas))
    enviar_botoes(numero, "Posso confirmar o pré-agendamento com esses dados?", [
        {"id": "confirma_cons", "title": "Confirmar"},
        {"id": "editar_cons", "title": "Editar"},
    ])


def perguntar_tipo_exame(numero: str):
    enviar_botoes(numero, "Qual exame você precisa?", EXAMES_BTNS_P1)
    time.sleep(0.6)
    enviar_botoes(numero, "Mais opções:", EXAMES_BTNS_P2)


def perguntar_pedido_medico(numero: str):
    enviar_botoes(numero, "Você possui *pedido médico* para esse exame?", SIM_NAO_BTNS)


# ------------------------------------------------------------
# Entrada principal
# ------------------------------------------------------------

def processar_texto(numero: str, texto: str, nome_exibicao: str | None = None):
    texto_l = (texto or "").strip()
    est = get_estado(numero)
    etapa = est.get("etapa", "inicio")

    # Primeiro contato
    if etapa == "inicio":
        # Captura nome de exibição se vier
        if nome_exibicao and not est["dados"].get("nome"):
            salvar_dado(numero, "nome", nome_exibicao)
            upsert_primeiro_atendimento({
                "numero_whatsapp": numero,
                "nome": nome_exibicao,
            })
        log_historico(numero, etapa="menu_inicial", acao="texto", valor=texto_l)
        boas_vindas(numero, est["dados"].get("nome"))
        return

    # Etapas de coleta Consulta
    if etapa == "cons_nome":
        salvar_dado(numero, "nome", texto_l)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "nome": texto_l})
        log_historico(numero, etapa="cons_nome", acao="texto", valor=texto_l, contexto={"tipo": "Consulta"})
        enviar_texto(numero, "Informe o *CPF* (apenas números):")
        set_etapa(numero, "cons_cpf")
        return

    if etapa == "cons_cpf":
        d = normalizar_cpf(texto_l)
        if not cpf_valido(d):
            enviar_texto(numero, "CPF inválido. Envie novamente (apenas números, 11 dígitos).")
            return
        salvar_dado(numero, "cpf", d)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "cpf": d})
        log_historico(numero, etapa="cons_cpf", acao="texto", valor=d, contexto={"tipo": "Consulta"})
        enviar_texto(numero, "Qual a *data de nascimento*? (DD/MM/AAAA)")
        set_etapa(numero, "cons_nasc")
        return

    if etapa == "cons_nasc":
        if not data_valida(texto_l):
            enviar_texto(numero, "Data inválida. Use o formato DD/MM/AAAA.")
            return
        salvar_dado(numero, "nascimento", texto_l)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "data_nascimento": texto_l})
        log_historico(numero, etapa="cons_nasc", acao="texto", valor=texto_l, contexto={"tipo": "Consulta"})

        if est["dados"].get("coletar_convenio"):
            enviar_texto(numero, "Qual o *convênio*? (Se não encontrar depois nos botões, digite aqui)")
            set_etapa(numero, "cons_convenio")
        else:
            perguntar_especialidade(numero)
            set_etapa(numero, "cons_esp")
        return

    if etapa == "cons_convenio":
        salvar_dado(numero, "convenio", texto_l)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "convenio": texto_l})
        log_historico(numero, etapa="cons_convenio", acao="texto", valor=texto_l, contexto={"tipo": "Consulta", "modalidade": "Convênio"})
        enviar_texto(numero, "Se tiver *número da carteirinha*, envie agora (ou diga 'pular').")
        set_etapa(numero, "cons_carteirinha")
        return

    if etapa == "cons_carteirinha":
        if texto_l.lower() != "pular":
            salvar_dado(numero, "carteirinha", texto_l)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "carteirinha": texto_l})
        log_historico(numero, etapa="cons_carteirinha", acao="texto", valor=texto_l, contexto={"tipo": "Consulta", "modalidade": "Convênio"})
        perguntar_especialidade(numero)
        set_etapa(numero, "cons_esp")
        return

    if etapa == "cons_esp_outro":
        salvar_dado(numero, "especialidade", texto_l)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "especialidade": texto_l})
        log_historico(numero, etapa="cons_esp_outro", acao="texto", valor=texto_l, contexto={"tipo": "Consulta"})
        perguntar_preferencia(numero)
        set_etapa(numero, "cons_pref")
        return

    # Exames texto
    if etapa == "exam_outro":
        salvar_dado(numero, "exame", texto_l)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "exame": texto_l})
        log_historico(numero, etapa="exam_outro", acao="texto", valor=texto_l, contexto={"tipo": "Exame"})
        perguntar_pedido_medico(numero)
        set_etapa(numero, "exam_pedido")
        return

    # Fallback: se digitou algo fora do esperado
    enviar_texto(numero, "Não entendi. Use os botões ou responda conforme solicitado. 😊")


def processar_botao(numero: str, button_id: str, nome_exibicao: str | None = None):
    est = get_estado(numero)
    etapa = est.get("etapa", "inicio")

    # Menu inicial
    if button_id in ("cons", "exam", "mais"):
        if nome_exibicao and not est["dados"].get("nome"):
            salvar_dado(numero, "nome", nome_exibicao)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "nome": nome_exibicao})
        if button_id == "cons":
            est["tipo"] = "Consulta"
            set_estado(numero, est)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "tipo": "Consulta"})
            log_historico(numero, etapa="menu_inicial", acao="clique_botao", valor="Consulta")
            perguntar_convenio_ou_particular(numero)
            set_etapa(numero, "cons_conv_part")
            return
        if button_id == "exam":
            est["tipo"] = "Exame"
            set_estado(numero, est)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "tipo": "Exame"})
            log_historico(numero, etapa="menu_inicial", acao="clique_botao", valor="Exames")
            perguntar_tipo_exame(numero)
            set_etapa(numero, "exam_tipo")
            return
        if button_id == "mais":
            log_historico(numero, etapa="menu_inicial", acao="clique_botao", valor="Mais opções")
            enviar_botoes(numero, "Escolha uma opção:", [
                {"id": "info_endereco", "title": "Endereço/Contato"},
                {"id": "info_horarios", "title": "Horários"},
                {"id": "humano", "title": "Falar com atendente"},
            ])
            set_etapa(numero, "mais_menu")
            return

    # Consulta: convênio/particular
    if etapa == "cons_conv_part" and button_id in ("cons_conv", "cons_part"):
        if button_id == "cons_conv":
            est["modalidade"] = "Convênio"
            set_estado(numero, est)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "modalidade": "Convênio"})
            log_historico(numero, etapa="cons_conv_part", acao="clique_botao", valor="Convênio", contexto={"tipo": "Consulta"})
            perguntar_dados_basicos(numero, incluir_convenio=True)
            return
        else:
            est["modalidade"] = "Particular"
            set_estado(numero, est)
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "modalidade": "Particular"})
            log_historico(numero, etapa="cons_conv_part", acao="clique_botao", valor="Particular", contexto={"tipo": "Consulta"})
            perguntar_dados_basicos(numero, incluir_convenio=False)
            return

    # Consulta: especialidade via botões
    if etapa == "cons_esp" and button_id.startswith("esp_"):
        mapa = {
            "esp_clinico": "Clínico Geral",
            "esp_pediatria": "Pediatria",
            "esp_gineco": "Ginecologia",
            "esp_cardio": "Cardiologia",
            "esp_orto": "Ortopedia",
            "esp_outro": "Outra",
        }
        escolha = mapa.get(button_id, "Outra")
        if escolha == "Outra":
            enviar_texto(numero, "Digite qual especialidade você procura:")
            set_etapa(numero, "cons_esp_outro")
            return
        salvar_dado(numero, "especialidade", escolha)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "especialidade": escolha})
        log_historico(numero, etapa="cons_esp", acao="clique_botao", valor=escolha, contexto={"tipo": "Consulta"})
        perguntar_preferencia(numero)
        set_etapa(numero, "cons_pref")
        return

    if etapa == "cons_pref" and button_id.startswith("pref_"):
        mapa = {
            "pref_manha": "Manhã",
            "pref_tarde": "Tarde",
            "pref_qualq": "Qualquer",
        }
        pref = mapa.get(button_id, "Qualquer")
        salvar_dado(numero, "preferencia", pref)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "preferencia_turno": pref})
        log_historico(numero, etapa="cons_pref", acao="clique_botao", valor=pref, contexto={"tipo": "Consulta"})
        resumo_confirmacao(numero)
        set_etapa(numero, "cons_confirma")
        return

    if etapa == "cons_confirma" and button_id in ("confirma_cons", "editar_cons"):
        if button_id == "confirma_cons":
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "status": "Aguardando"})
            log_historico(numero, etapa="cons_confirma", acao="clique_botao", valor="Confirmar", contexto={"tipo": "Consulta"})
            enviar_texto(numero, "Perfeito! Seus dados foram registrados. Nossa equipe entrará em contato para confirmar o horário. ✅")
            limpar_estado(numero)
            return
        else:
            log_historico(numero, etapa="cons_confirma", acao="clique_botao", valor="Editar", contexto={"tipo": "Consulta"})
            perguntar_convenio_ou_particular(numero)
            set_etapa(numero, "cons_conv_part")
            return

    # Exames: tipo via botões
    if etapa == "exam_tipo" and button_id.startswith("ex_"):
        mapa = {
            "ex_hemo": "Hemograma",
            "ex_raiox": "Raio-X",
            "ex_ultra": "Ultrassom",
            "ex_eletro": "Eletrocardiograma",
            "ex_urina": "Urina",
            "ex_outro": "Outro",
        }
        escolha = mapa.get(button_id, "Outro")
        if escolha == "Outro":
            enviar_texto(numero, "Digite qual exame você precisa:")
            set_etapa(numero, "exam_outro")
            return
        salvar_dado(numero, "exame", escolha)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "exame": escolha})
        log_historico(numero, etapa="exam_tipo", acao="clique_botao", valor=escolha, contexto={"tipo": "Exame"})
        perguntar_pedido_medico(numero)
        set_etapa(numero, "exam_pedido")
        return

    if etapa == "exam_pedido" and button_id in ("sim", "nao"):
        pm = "Sim" if button_id == "sim" else "Não"
        salvar_dado(numero, "pedido_medico", pm)
        upsert_primeiro_atendimento({"numero_whatsapp": numero, "pedido_medico": pm})
        log_historico(numero, etapa="exam_pedido", acao="clique_botao", valor=pm, contexto={"tipo": "Exame"})
        # Coletar dados básicos (nome, cpf, nascimento) depois do pedido médico
        enviar_texto(numero, "Informe o *nome completo* do paciente.")
        set_etapa(numero, "cons_nome")  # Reaproveitamos as etapas de coleta de dados
        return

    # Mais opções
    if etapa == "mais_menu":
        if button_id == "info_endereco":
            log_historico(numero, etapa="mais_menu", acao="clique_botao", valor="Endereço/Contato")
            enviar_texto(numero, "📍 Endereço: Av. São Miguel, 7900 – CEP 08070-001\n☎️ Contato: (11) 98878-0161")
            boas_vindas(numero, est["dados"].get("nome"))
            set_etapa(numero, "inicio")
            return
        if button_id == "info_horarios":
            log_historico(numero, etapa="mais_menu", acao="clique_botao", valor="Horários")
            enviar_texto(numero, "⏰ Atendemos de segunda a sexta, 8h às 18h (ajuste conforme a clínica).")
            boas_vindas(numero, est["dados"].get("nome"))
            set_etapa(numero, "inicio")
            return
        if button_id == "humano":
            log_historico(numero, etapa="mais_menu", acao="clique_botao", valor="Falar com atendente")
            upsert_primeiro_atendimento({"numero_whatsapp": numero, "status": "Encaminhado humano"})
            enviar_texto(numero, "Certo! Vou te transferir para um atendente humano. Aguarde um instante, por favor.")
            limpar_estado(numero)
            return

    # Se nada casou
    enviar_texto(numero, "Não entendi. Use os botões ou responda conforme solicitado. 😊")


# ------------------------------------------------------------
# Entrada pública a partir do webhook
# ------------------------------------------------------------

def responder(evento: dict):
    """
    Entrada principal. Chame esta função a partir do webhook:
    - Para mensagens de texto: chama processar_texto
    - Para botões (interactive replies): chama processar_botao
    """
    try:
        entry = evento.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        contacts = value.get("contacts", [])
        nome_exibicao = contacts[0].get("profile", {}).get("name") if contacts else None

        for msg in value.get("messages", []):
            numero = msg.get("from")
            tipo = msg.get("type")

            # Garante cartão do cliente no primeiro contato
            upsert_primeiro_atendimento({
                "numero_whatsapp": numero,
                "ultimo_timestamp": agora_iso(),
            })

            if tipo == "text":
                texto = msg.get("text", {}).get("body", "")
                processar_texto(numero, texto, nome_exibicao)
                continue

            if tipo == "interactive":
                interactive = msg.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    button_id = interactive.get("button_reply", {}).get("id")
                    processar_botao(numero, button_id, nome_exibicao)
                    continue

            # Outros tipos (image, document, etc.)
            enviar_texto(numero, "Recebi seu conteúdo. Por favor, use os botões ou responda conforme solicitado.")
    except Exception as e:
        print("[Responder] Erro ao processar evento:", e)


# ------------------------------------------------------------
# Funções de verificação do webhook (usadas no webhook.py)
# ------------------------------------------------------------

def verify_token(token_enviado: str) -> bool:
    return token_enviado == VERIFY_TOKEN


# ------------------------------------------------------------
# Notas de integração com webhook.py
# ------------------------------------------------------------
# - Seu webhook.py deve chamar `verify_token` na verificação GET.
# - No POST, repasse o JSON completo para `responder(evento)`.
#   Exemplo (Flask):
#
# @app.route('/webhook', methods=['GET'])
# def webhook_verify():
#     mode = request.args.get('hub.mode')
#     token = request.args.get('hub.verify_token')
#     challenge = request.args.get('hub.challenge')
#     if mode == 'subscribe' and verify_token(token):
#         return challenge, 200
#     return 'Token inválido', 403
#
# @app.route('/webhook', methods=['POST'])
# def webhook_receive():
#     data = request.get_json()
#     responder(data)
#     return 'EVENT_RECEIVED', 200
#
# Observação importante:
# - Este arquivo envia mensagens diretamente à API do WhatsApp Cloud.
# - Se você já tem utilitários próprios (send_message, enviar_botoes etc.),
#   você pode substituir `enviar_texto` e `enviar_botoes` por seus wrappers
#   para manter logs e consistência.
# - Para Google Sheets, plugue suas funções nas seções "Hook opcional".
