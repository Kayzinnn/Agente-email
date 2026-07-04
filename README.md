# Agente de organização de e-mails (Gmail + Claude API)

Script Python que você roda **manualmente** quando quiser organizar sua caixa
de entrada. Não fica rodando sozinho, não envia e-mails automaticamente — só
cria rascunhos para você revisar antes de enviar.

## O que ele faz

1. Lê os e-mails dos últimos N dias da sua conta Gmail
2. Aplica labels automáticas com base em regras: **Reuniões**, **Documentos**,
   **Propagandas**, **Pendências**
3. Gera um resumo (digest) do que chegou, usando a API da Anthropic (Claude)
4. (Opcional) Sugere rascunhos de resposta para os e-mails marcados como
   pendência

## Passo 1 — Ativar a API do Gmail

1. Acesse https://console.cloud.google.com/
2. Crie um projeto novo (ou use um existente)
3. Vá em **APIs e serviços → Biblioteca**, procure **Gmail API** e clique em
   **Ativar**
4. Vá em **APIs e serviços → Tela de consentimento OAuth**:
   - Tipo de usuário: **Externo** (contas pessoais) ou **Interno** (contas
     Google Workspace, se você tiver permissão de admin)
   - Preencha nome do app, e-mail de suporte etc.
   - Em "Escopos", não precisa adicionar nada manualmente
   - Adicione seu próprio e-mail em "Usuários de teste" se ficar em modo de
     teste
5. Vá em **APIs e serviços → Credenciais → Criar credenciais → ID do cliente
   OAuth**
   - Tipo de aplicativo: **App para computador (Desktop app)**
   - Baixe o arquivo JSON gerado **na hora** (o Google só permite baixar a
     chave secreta uma vez), renomeie para `credentials.json` e coloque na
     mesma pasta do `main.py`

> Importante: essas credenciais ficam só no seu computador. Você mesmo
> autoriza o acesso pelo navegador na primeira execução — a aplicação não
> lida com sua senha ou login do Google em nenhum momento.

## Passo 2 — Criar ambiente virtual e instalar dependências

```bash
python -m venv venv
# Windows
venv\Scripts\Activate.ps1
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

## Passo 3 — Configurar a chave da Anthropic (opcional, para resumo e rascunhos)

```bash
export ANTHROPIC_API_KEY="sua-chave-aqui"      # macOS/Linux
$env:ANTHROPIC_API_KEY="sua-chave-aqui"        # Windows PowerShell
```

Sem essa variável, o script ainda categoriza e-mails e detecta pendências
normalmente — só pula a parte de resumo/rascunho com IA.

## Passo 4 — Rodar

```bash
# Resumo dos últimos 7 dias (padrão)
python main.py

# Últimos 3 dias
python main.py --dias 3

# Também criar rascunhos de resposta no Gmail para as pendências
python main.py --criar-rascunhos

# Só categorizar, sem chamar a API da Anthropic
python main.py --sem-resumo
```

Na primeira execução, uma aba do navegador vai abrir pedindo para você
autorizar o acesso à sua conta Gmail. Depois disso, um arquivo `token.json` é
salvo localmente e você não precisa autorizar de novo (a menos que expire).

## Personalizando as regras

Abra `main.py` e edite o dicionário `CATEGORIAS` no topo do arquivo — dá para
adicionar novas categorias, palavras-chave de assunto, remetente, corpo, ou
tipos de anexo. A lista `PALAVRAS_PENDENCIA` controla o que é detectado como
"precisa de resposta".

## Roadmap / próximas melhorias

- [ ] Separar em módulos (`auth.py`, `gmail_client.py`, `categorizer.py`,
      `ai_assistant.py`)
- [ ] Testes automatizados para as funções de categorização
- [ ] Classificação assistida por IA para casos que as regras não cobrem
- [ ] Digest diário salvo em arquivo Markdown com histórico

## Segurança

- O script só pede o escopo `gmail.modify` (ler, rotular, criar rascunhos) —
  **não tem permissão de enviar e-mails** de propósito, só cria rascunhos.
- Revise sempre os rascunhos antes de enviar.
- `credentials.json` e `token.json` são sensíveis — nunca vão para o
  repositório (já estão no `.gitignore`).
