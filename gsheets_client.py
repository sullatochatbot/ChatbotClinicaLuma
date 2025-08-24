# gsheets_client.py
import os, json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID = os.getenv("CLINICA_SHEET_ID")

def _service():
  creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
  if not creds_json:
    raise RuntimeError("Env var GOOGLE_CREDENTIALS_JSON ausente")
  info = json.loads(creds_json)
  creds = Credentials.from_service_account_info(info, scopes=SCOPES)
  return build("sheets", "v4", credentials=creds, cache_discovery=False)

def _append(aba: str, values: list):
  body = {"values": [values]}
  return _service().spreadsheets().values().append(
    spreadsheetId=SHEET_ID, range=f"{aba}!A:Z",
    valueInputOption="USER_ENTERED",
    insertDataOption="INSERT_ROWS", body=body
  ).execute()

# atalhos usados pelo bot
def salvar_paciente(cpf, nome, data_nasc, endereco, contato,
                    tipo_atend, conv_part, esp_ou_exame, origem, ts_criado, ts_atualizado):
  return _append("Pacientes", [cpf, nome, data_nasc, endereco, contato,
                               tipo_atend, conv_part, esp_ou_exame, origem, ts_criado, ts_atualizado])

def salvar_solicitacao(ts, cpf, tipo, detalhe, status, obs):
  return _append("Solicitacoes", [ts, cpf, tipo, detalhe, status, obs])

def salvar_pesquisa(ts, cpf, tipo, texto):
  return _append("Pesquisa", [ts, cpf, tipo, texto])

def registrar_interacao(ts, cpf, evento, detalhe):
  return _append("Interacoes", [ts, cpf, evento, detalhe])
