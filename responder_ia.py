import os
from typing import Optional

def responder_com_ia(mensagem: str, nome: Optional[str] = None) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import anthropic
        print(f"✅ anthropic instalado: {anthropic.__version__}")
        client = anthropic.Anthropic(api_key=api_key)

        sistema = (
            "Você é o assistente virtual da Clínica Luma, clínica médica em São Paulo. "
            "Endereço: Rua Utrecht, 129 – Vila Rio Branco – CEP 03878-000. "
            "Especialidades: Clínico Geral, Dermatologia, Dentista, Endocrinologia, Fonoaudiologia, "
            "Harmonização Facial, Medicina do Trabalho, Nutrição/Medicina Esportiva, Ortopedia, Pediatria, Psiquiatria. "
            "Exames: Admissional/Demissional, Laboratoriais, Eletrocardiograma, Raio X, Toxicológico. "
            "Atendimento: convênio e particular. Horário: segunda a sexta das 9h às 17h. "
            "Contato: Fixo (11) 2043-9937 | WhatsApp (11) 97537-9655. "
            "Agendamento online (Doctoralia): https://www.doctoralia.com.br/clinicas/luma-clinica-da-familia. "
            "Responda sempre em português brasileiro, com tom acolhedor e direto, em 1 a 3 frases. "
            "Quando o paciente perguntar sobre agendamento, informe que pode usar o menu do WhatsApp ou agendar diretamente pelo Doctoralia. "
            "Nunca marque consultas diretamente. "
            "Quando fizer sentido, sugira que o paciente escolha uma opção no menu."
        )

        usuario = mensagem if not nome else f"[Paciente: {nome}]\n{mensagem}"

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=sistema,
            messages=[{"role": "user", "content": usuario}],
        )
        texto = (resp.content[0].text or "").strip()
        return texto if texto else None

    except Exception as e:
        print("⚠️ Claude indisponível:", e)
        return None
