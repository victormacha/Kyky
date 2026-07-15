# Kyky

Sua IA pessoal, na nuvem, de graça, com link pra compartilhar com amigos.
Você é reconhecida como administradora automaticamente (a primeira conta
criada no sistema vira admin).

**Stack usada, 100% gratuita:**
- **Groq** — roda o modelo de IA (Llama 3.3 / Llama 4 Scout para imagens) na
  nuvem, sem custo, sem cartão
- **Render** — hospeda o site, te dá uma URL pública gratuita

## O que tem de novo nessa versão

- **Visual novo**, com identidade própria (o "orbe" gradiente é o avatar
  padrão da Kyky, e vira moldura pro seu ícone quando você troca um).
- **Histórico de conversas**: cada conversa fica salva e listada na barra
  lateral, com título automático, renomear e excluir.
- **Imagens, PDFs e arquivos de texto**: dá pra anexar no chat (clipe 📎).
  Imagens usam automaticamente um modelo com visão (Llama 4 Scout); PDFs e
  `.txt`/`.md`/`.csv` têm o texto extraído e enviado como contexto.
- **Painel de administração** (só pra você, o admin):
  - **Visão geral**: total de usuários, ativos nas últimas 24h/7 dias,
    total de conversas e mensagens, gráfico simples de uso por dia.
  - **Personalidade**: editar o nome dela, notas de tom/personalidade,
    trocar de modelo, e trocar o ícone — tudo aplicado na hora.
  - **Usuários**: lista de quem tem acesso, com opção de remover.
  - **Sugestões**: veja o que a Kyky sugeriu de código (ver abaixo).
- **Autoedição (com limites de segurança propositais)**:
  - Quando você fala com ela, ela pode ajustar o próprio **nome** e as
    **notas de personalidade** na hora, se você pedir (ex: "muda seu tom
    pra ser mais direta"). Isso é gravado em `config.json`, nunca no
    código-fonte, e você sempre pode restaurar o padrão pelo painel.
  - Ela também pode **sugerir código** para novas funcionalidades do
    próprio sistema quando você pedir. A sugestão fica pendente no painel
    ("Sugestões") pra você ler, copiar e aplicar manualmente — ela nunca
    escreve nem executa código no servidor sozinha. Isso é proposital: dar
    a uma IA exposta ao público (seus amigos também usam o mesmo sistema)
    a capacidade de editar e rodar seu próprio código automaticamente é um
    risco real de segurança, então essa parte fica sempre sob seu controle.
  - Essas ferramentas só ficam disponíveis nas conversas com você
    (administradora); amigos convidados não têm acesso a elas.

## Passo a passo

### 1. Pegue uma chave gratuita do Groq

1. Crie conta em **console.groq.com** (com e-mail ou Google, sem cartão)
2. Vá em **API Keys** → **Create API Key**
3. Copie a chave (começa com `gsk_...`) — você vai usar no passo 3

### 2. Suba o código pro GitHub

Crie um repositório novo no GitHub e suba a pasta `kyky` (ou o nome que
você preferir) inteira pra lá. Se nunca fez isso:

```bash
cd kyky
git init
git add .
git commit -m "kyky com histórico, anexos e painel admin"
```

Depois crie um repositório vazio no GitHub, e siga as instruções que ele
mostra pra "push an existing repository".

### 3. Deploy no Render

1. Crie conta em **render.com** (dá pra usar login do GitHub)
2. Clique em **New** → **Web Service**
3. Conecte o repositório que você acabou de subir
4. Configurações:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Plano:** Free
5. Em **Environment Variables**, adicione:
   - `GROQ_API_KEY` = a chave que você copiou no passo 1
6. Clique em **Create Web Service**

Depois de alguns minutos, o Render te dá uma URL tipo
`https://kyky-xxxx.onrender.com` — esse é o link que você manda pros seus
amigos.

### 4. Cadastre-se como admin

Assim que o site subir, entre no link e **cadastre-se primeiro** — a
primeira conta criada vira automaticamente administradora. Depois disso
seus amigos podem se cadastrar normalmente (eles entram como usuários
comuns, sem acesso ao painel admin nem às ferramentas de autoedição).

## Coisas importantes de saber sobre o plano gratuito

- **O Render "dorme" depois de 15 minutos sem uso.** Quando alguém acessa
  depois disso, a primeira resposta demora ~30-60 segundos pra "acordar"
  o servidor. Depois disso fica rápido normalmente. Isso é esperado, não
  é bug.
- **O armazenamento do plano gratuito não é permanente.** Se o Render
  reiniciar o serviço (o que acontece de vez em quando), o banco de dados
  (`kyky.db`), as memórias de conversa, o `config.json` e o ícone
  customizado podem ser apagados. Pra um teste com amigos tá ótimo; se um
  dia quiser que os dados fiquem salvos de verdade, dá pra conectar um
  banco/disco externo gratuito (ex: um Postgres grátis do próprio Render
  ou do Supabase, ou um "Persistent Disk" pago do Render).
- **A chave GROQ_API_KEY** fica só no Render, nunca no código que você
  sobe pro GitHub — assim ninguém mais consegue usá-la.
- **Anexos**: o limite é 15MB por arquivo. Imagens ficam guardadas no
  histórico da conversa em base64 (ou seja, conversas com muitas fotos
  ficam "pesadas" — isso é esperado).

## Painel de administração

Acesse pelo botão "Painel admin" na barra lateral (só aparece pra você).
De lá dá pra ver estatísticas de uso, editar a personalidade da Kyky,
trocar o ícone, gerenciar usuários e revisar sugestões de código que ela
mesma propôs.

Se preferir mexer direto pela API:
- `GET /admin/stats` — estatísticas de uso
- `GET /admin/users` / `DELETE /admin/users/{username}`
- `GET|POST /admin/config` — personalidade/modelo
- `POST /admin/icon` — trocar ícone (multipart, campo `file`)
- `GET /admin/suggestions` / `POST /admin/suggestions/{id}/status` / `DELETE /admin/suggestions/{id}`

(mandando o header `Authorization: Bearer SEU_TOKEN`)

## Personalizando a Kyky

Duas formas:
1. **Painel admin → Personalidade** (recomendado): nome, notas de tom,
   modelo e ícone, aplicado na hora, sem precisar mexer em código.
2. **No código**, em `main.py`: `BASE_PERSONALITY_TEMPLATE` guarda os
   princípios fixos (segurança, honestidade) que não são editáveis pela
   IA nem pelo painel — mude aqui só se quiser alterar essas regras de
   base.

## Rodando localmente antes de subir (recomendado)

```bash
pip install -r requirements.txt
export GROQ_API_KEY="sua-chave-aqui"
python main.py
```
Abra `http://localhost:8000`, cadastre-se, teste — só depois suba pro
GitHub/Render.

## Próximos passos possíveis

- Aplicar sugestões de código automaticamente via GitHub Actions/PR (hoje
  é manual, de propósito, por segurança)
- Banco de dados persistente (Postgres gratuito)
- Plugar essa mesma "cabeça" no seu bot de Discord
