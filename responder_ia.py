import os
import openai
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

def responder_com_ia(pergunta):
    try:
        resposta = openai.ChatCompletion.create(
            model="gpt-4",  # ou "gpt-3.5-turbo" se preferir custo menor
            messages=[
                {"role": "system", "content": "Você é um atendente do Grupo Sullato, especializada em venda de veículos de passeio e utilitários. Seja sempre claro, simpático e direto ao ponto."},
                {"role": "user", "content": pergunta}
            ],
            temperature=0.5,
            max_tokens=300
        )
        texto = resposta.choices[0].message['content'].strip()
        return texto
    except Exception as e:
        print("❌ Erro ao gerar resposta com IA:", e)
        return "Desculpe, não consegui entender sua mensagem. Pode tentar reformular?"
