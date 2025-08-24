# Clínica Luma — Chatbot (WhatsApp) + Google Sheets (Guia Completo)

Assistente de atendimento da **Clínica Luma** integrado ao **WhatsApp Cloud API (Meta)**, com backend leve em **Google Sheets** para cadastro, solicitações, deduplicação de pacientes, campanhas e logs.  
Este arquivo é o **manual único** do projeto: explica a planilha, o servidor, as variáveis e o deploy.

---

## 1) Visão geral do fluxo

**Usuário → WhatsApp → Webhook (Flask) → Google Sheets**

- Menu inicial: **Consulta** | **Exames** | **+ Opções**
- Consulta/Exames → pergunta **Convênio ou Particular** → coleta:
  - Nome completo, **CPF**, Data de nascimento (DD/MM/AAAA), **Endereço**, **Especialidade** (ou **Exame**)
- + Opções → **Retorno de consulta** | **Resultado de exames**
  - Pede **CPF**, busca no Sheets; se não achar, volta para a coleta completa
- Fechamento padrão: **“Um atendente irá entrar em contato”**
- Gravação no Sheets: `Pacientes`, `Solicitacoes`, `Pesquisa`, `Interacoes`, `Logs`
- Base única para campanhas: `Clientes_Unicos` (deduplicado por CPF)
- **Lista_Envio** diária: aniversários + campanhas (natal/ano novo/mães/pais)

---

## 2) Google Sheets (backend da clínica)

### 2.1 Abas/colunas
- **Pacientes**  
  `cpf, nome, data_nasc, endereco, contato, tipo_atendimento, convenio_ou_particular, especialidade_ou_exame, origem, ts_criado, ts_atualizado`

- **Solicitacoes**  
  `ts, cpf, tipo (retorno|resultado|agendamento), detalhe, status_interno, observacoes`

- **Pesquisa**  
  `ts, cpf, tipo (especialidade|exame), texto_digitado`

- **Interacoes**  
  `ts, cpf, evento, detalhe`

- **Clientes_Unicos** (gerado pela dedup)  
  `cpf, nome, data_nasc, endereco, contato, primeiro_registro, ultimo_registro, origem_mais_recente`  
  → **1 linha por CPF** (pega o **último** registro por `ts_atualizado`)

- **Campanhas**  
  `campanha, data (DD/MM ou VAR), descricao`  
  → Ex.: `natal | 25/12`, `ano_novo | 01/01`, `dia_das_maes | VAR`, `dia_dos_pais | VAR`

- **Lista_Envio** (gerada diariamente)  
  `ts_gerado, cpf, nome, contato, motivo, data_ref, status_envio, template`  
  → Evita duplicados por chave `cpf|motivo|data_ref`

- **Logs**  
  `ts, nivel, origem, acao, detalhe, cpf, chave, status`

### 2.2 Regras de dados
- **CPF**: somente dígitos (11)  
- **Data de nascimento**: `DD/MM/AAAA`  
- **Timestamps**: `YYYY-MM-DD HH:MM:SS`  
- **Contato**: telefone/WhatsApp ou e-mail (texto livre)

### 2.3 Automação (Apps Script)
No editor do Apps Script da planilha há um menu **“Clínica Luma”** com:

- **⚡ Instalação completa (1 clique)** → roda tudo:
  - cria/ajusta abas e formatos
  - insere campanhas padrão
  - executa **dedup** (`Clientes_Unicos`)
  - cria **Lista_Envio** do dia
  - recria **acionadores** (08:00 dedup / 09:00 lista)
- Ações individuais: `setupClinicaSheets`, `criarCampanhasPadrao`, `atualizarClientesUnicos`, `gerarListaEnvioHoje`, `recriarAcionadores`
- Tudo é registrado em **Logs**

> **Opcional**: API **Web App** do Apps Script (`doPost`) protegida por `API_SECRET`, caso prefira postar direto na planilha em vez de usar a Google Sheets API.

---

## 3) Estrutura recomendada do repositório

