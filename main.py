"""
Vamos criar um agente de email que vai ler os emails e organiza-los em pastas de acordo com o assunto do email.
Ele vai trabalhar pontualmente quando for acionado, sem rodar automaticamente.

O que ele faz numa execução:
  1. Autentica na sua conta Gmail (OAuth do próprio Google, no seu navegador)
  2. Busca e-mails dos últimos N dias
  3. Categoriza e aplica labels no Gmail: "Reuniões" e "Documentos"
  4. Identifica e-mails que parecem pendências (cobrança, prazo, "aguardo retorno" etc.)
  5. Gera um resumo (digest) do que chegou, usando a API da Anthropic
  6. Opcionalmente, sugere rascunhos de resposta para as pendências
     (fica salvo como RASCUNHO no Gmail — o script nunca envia nada sozinho).

Uso:
    python main.py                        # resumo dos últimos 7 dias
    python main.py --dias 3                # últimos 3 dias
    python main.py --criar-rascunhos       # também cria rascunhos de resposta no Gmail
    python main.py --sem-resumo            # só categoriza, não chama a API da Anthropic

"""
import argparse # permite criar uma interface de linha de comando
import os # permite manipular arquivos e diretórios do sistema operacional
import base64 # permite codificar e decodificar dados em base64 (usado para anexos de email)
import re # permite usar expressões regulares para buscar padrões em strings
from datetime import datetime 

from google.auth.transport.requests import Request # realiza requisições HTTP autenticadas
from google.oauth2.credentials import Credentials # representa as credenciais de autenticação do usuário
from google_auth_oauthlib.flow import InstalledAppFlow # gerencia o fluxo de autenticação OAuth 2.0 para aplicativos instalados
from googleapiclient.discovery import build # cria um cliente para acessar a API do Gmail
from googleapiclient.errors import HttpError # trata erros retornados pela API do Gmail
#_______________________________________________________________


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CATEGORIAS = {
    "Reuniões": {
        "assunto_contem": [
            "reunião", "reuniao", "meeting", "convite", "invite",
            "call", "alinhamento", "daily", "sync",
        ],
        "anexo_ics": True,
    },
    "Documentos": {
        "assunto_contem": [
            "documento", "contrato", "relatório", "relatorio",
            "proposta", "anexo", "planilha", "onboarding",
        ],
        "extensoes_anexo": [".pdf", ".docx", ".xlsx", ".pptx", ".csv"],
    },
    "Propagandas": {
        "assunto_contem": [
            "promoção", "promocao", "desconto", "oferta", "cupom",
            "black friday", "sale", "grátis", "gratis", "%","itens","pedido","garanta","compras","compre","frete","entrega","loja","venda","produto","novidade","lançamento","lancamento"
        ],
        "remetente_contem": [
            "newsletter", "marketing", "no-reply", "noreply", "promo",
        ],
        "corpo_contem": ["cancelar inscrição", "unsubscribe", "descadastrar"],
    },
}

PALAVRAS_PENDENCIA = [
    "aguardo retorno", "aguardo seu retorno", "aguardo confirmação",
    "por favor responda", "poderia confirmar", "preciso da sua resposta",
    "prazo até", "prazo final", "urgente", "fico no aguardo",
    "gentileza confirmar", "até quando",
]

DIAS_PADRAO = 100
# ------------------------------------------------------------------
# AUTENTICAÇÃO
# ------------------------------------------------------------------
 
def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Não encontrei '{CREDENTIALS_FILE}'. Siga o README para gerar "
                    "suas credenciais OAuth no Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

# ------------------------------------------------
# LEITURA DE E-MAILS
# ------------------------------------------------

 
def listar_mensagens(service, dias):
    query = f"newer_than:{dias}d -in:chats"
    resultado = []
    resp = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    resultado.extend(resp.get("messages", []))
    while "nextPageToken" in resp:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=resp["nextPageToken"]
        ).execute()
        resultado.extend(resp.get("messages", []))
    return resultado
 
 
def _get_header(headers, nome):
    for h in headers:
        if h["name"].lower() == nome.lower():
            return h["value"]
    return ""
 
 
def _extrair_corpo(payload):
    """Extrai texto simples do corpo do e-mail (lida com multipart)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
 
    if payload.get("parts"):
        for part in payload["parts"]:
            texto = _extrair_corpo(part)
            if texto:
                return texto
 
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        return re.sub("<[^<]+?>", " ", html)
 
    return ""
 
 
def _extrair_anexos(payload):
    anexos = []
    def walk(p):
        if p.get("filename"):
            anexos.append(p["filename"])
        for part in p.get("parts", []) or []:
            walk(part)
    walk(payload)
    return anexos
 
 
def obter_detalhes(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    headers = msg["payload"].get("headers", [])
    corpo = _extrair_corpo(msg["payload"])
    anexos = _extrair_anexos(msg["payload"])
    return {
        "id": msg_id,
        "thread_id": msg.get("threadId"),
        "remetente": _get_header(headers, "From"),
        "assunto": _get_header(headers, "Subject"),
        "data": _get_header(headers, "Date"),
        "corpo": corpo[:3000],
        "anexos": anexos,
        "snippet": msg.get("snippet", ""),
    }
 
# ------------------------------------------------------------------
# CATEGORIZAÇÃO
# ------------------------------------------------------------------
 
def categorizar(detalhe):
    categorias_encontradas = []
    assunto = detalhe["assunto"].lower()
    anexos = [a.lower() for a in detalhe["anexos"]]
    remetente = detalhe["remetente"].lower()
    corpo = detalhe["corpo"].lower()

    regras_reuniao = CATEGORIAS["Reuniões"]
    if any(p in assunto for p in regras_reuniao["assunto_contem"]):
        categorias_encontradas.append("Reuniões")
    elif any(a.endswith(".ics") for a in anexos):
        categorias_encontradas.append("Reuniões")

    regras_doc = CATEGORIAS["Documentos"]
    if any(p in assunto for p in regras_doc["assunto_contem"]):
        if "Documentos" not in categorias_encontradas:
            categorias_encontradas.append("Documentos")
    elif any(any(a.endswith(ext) for ext in regras_doc["extensoes_anexo"]) for a in anexos):
        if "Documentos" not in categorias_encontradas:
            categorias_encontradas.append("Documentos")

    regras_prop = CATEGORIAS["Propagandas"]
    eh_propaganda = (
        any(p in assunto for p in regras_prop["assunto_contem"])
        or any(p in remetente for p in regras_prop["remetente_contem"])
        or any(p in corpo for p in regras_prop["corpo_contem"])
    )
    if eh_propaganda and "Propagandas" not in categorias_encontradas:
        categorias_encontradas.append("Propagandas")

    return categorias_encontradas
 
 
def eh_pendencia(detalhe):
    texto = (detalhe["assunto"] + " " + detalhe["corpo"]).lower()
    return any(p in texto for p in PALAVRAS_PENDENCIA)
 
 
# ------------------------------------------------------------------
# LABELS NO GMAIL
# ------------------------------------------------------------------
 
def garantir_labels(service, nomes):
    existentes = service.users().labels().list(userId="me").execute().get("labels", [])
    mapa = {l["name"]: l["id"] for l in existentes}
    for nome in nomes:
        if nome not in mapa:
            nova = service.users().labels().create(
                userId="me",
                body={"name": nome, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
            ).execute()
            mapa[nome] = nova["id"]
    return mapa
 
 
def aplicar_labels(service, msg_id, label_ids):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": label_ids}
    ).execute()
 
 
# ------------------------------------------------------------------
# RESUMO E RASCUNHOS COM A API DA ANTHROPIC
# ------------------------------------------------------------------
 
def gerar_resumo(mensagens):
    import anthropic
 
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[Aviso] ANTHROPIC_API_KEY não definida — pulando o resumo com IA.")
        print("Defina a variável de ambiente para ativar essa parte (veja o README).\n")
        return None
 
    client = anthropic.Anthropic(api_key=api_key)
 
    bloco = []
    for m in mensagens:
        bloco.append(
            f"- De: {m['remetente']}\n  Assunto: {m['assunto']}\n  Trecho: {m['snippet']}"
        )
    texto_emails = "\n".join(bloco)
 
    prompt = (
        "Você vai receber uma lista de e-mails recentes de uma pessoa que trabalha na "
        "empresa Estuda.com. Gere um resumo executivo curto em português, organizado em "
        "tópicos: (1) Reuniões marcadas, (2) Documentos recebidos, (3) Pendências que "
        "parecem exigir resposta, (4) Outros itens relevantes. Seja direto e objetivo.\n\n"
        f"E-mails:\n{texto_emails}"
    )
 
    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resposta.content if b.type == "text")
 
 
def sugerir_rascunho(client, detalhe):
    prompt = (
        "Escreva um rascunho de resposta em português, tom profissional e cordial, "
        "para o e-mail abaixo. Seja breve e direto. Responda APENAS com o corpo do "
        "e-mail, sem assunto e sem saudação de assinatura no final.\n\n"
        f"De: {detalhe['remetente']}\nAssunto: {detalhe['assunto']}\nCorpo:\n{detalhe['corpo']}"
    )
    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resposta.content if b.type == "text")
 
 
def criar_rascunho_gmail(service, detalhe, corpo_resposta):
    import email
    from email.mime.text import MIMEText
 
    remetente_email = re.search(r"<(.+?)>", detalhe["remetente"])
    destinatario = remetente_email.group(1) if remetente_email else detalhe["remetente"]
 
    msg = MIMEText(corpo_resposta)
    msg["to"] = destinatario
    msg["subject"] = "Re: " + detalhe["assunto"]
 
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"message": {"raw": raw, "threadId": detalhe["thread_id"]}}
    service.users().drafts().create(userId="me", body=body).execute()
 
 
# ------------------------------------------------------------------
# ORQUESTRAÇÃO
# ------------------------------------------------------------------
 
def main():
    parser = argparse.ArgumentParser(description="Agente de organização de e-mails Estuda.com")
    parser.add_argument("--dias", type=int, default=DIAS_PADRAO, help="Quantos dias olhar para trás")
    parser.add_argument("--criar-rascunhos", action="store_true", help="Cria rascunhos de resposta no Gmail para pendências")
    parser.add_argument("--sem-resumo", action="store_true", help="Não chama a API da Anthropic para gerar resumo")
    args = parser.parse_args()
 
    print(f"Conectando ao Gmail... (primeira vez abre o navegador para autorizar)")
    service = get_service()
 
    print(f"Buscando e-mails dos últimos {args.dias} dia(s)...")
    ids = listar_mensagens(service, args.dias)
    print(f"{len(ids)} e-mail(s) encontrado(s).")
 
    if not ids:
        print("Nada para organizar por enquanto.")
        return
 
    labels_map = garantir_labels(service, list(CATEGORIAS.keys()) + ["Pendências"])
 
    detalhes = []
    pendencias = []
 
    for item in ids:
        d = obter_detalhes(service, item["id"])
        detalhes.append(d)
 
        cats = categorizar(d)
        label_ids = [labels_map[c] for c in cats]
 
        if eh_pendencia(d):
            label_ids.append(labels_map["Pendências"])
            pendencias.append(d)
 
        if label_ids:
            aplicar_labels(service, d["id"], label_ids)
 
    print(f"\nCategorizados: {sum(1 for d in detalhes if categorizar(d))} e-mail(s)")
    print(f"Pendências detectadas: {len(pendencias)}")
 
    if not args.sem_resumo:
        print("\nGerando resumo com IA...")
        resumo = gerar_resumo(detalhes)
        if resumo:
            print("\n" + "=" * 60)
            print("RESUMO DA CAIXA DE ENTRADA")
            print("=" * 60)
            print(resumo)
            print("=" * 60)
 
    if args.criar_rascunhos and pendencias:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("\n[Aviso] ANTHROPIC_API_KEY não definida — não é possível criar rascunhos.")
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            print(f"\nCriando {len(pendencias)} rascunho(s) de resposta no Gmail...")
            for d in pendencias:
                corpo = sugerir_rascunho(client, d)
                criar_rascunho_gmail(service, d, corpo)
                print(f"  - Rascunho criado para: {d['assunto'][:60]}")
            print("Pronto — revise os rascunhos na sua caixa do Gmail antes de enviar.")
 
    print("\nConcluído.")
 
 
if __name__ == "__main__":
    main()