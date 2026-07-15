# Kyky

Sua IA pessoal, na nuvem, de graça, com link pra compartilhar com amigos.
Você é reconhecida como administradora automaticamente (a primeira conta
criada no sistema vira admin).

**Stack usada, 100% gratuita:**
- **Groq** — roda o modelo de IA (Llama 3.3) na nuvem, sem custo, sem cartão
- **Render** — hospeda o site, te dá uma URL pública gratuita

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
git commit -m "primeira versão do Kyky"
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
comuns).

## Coisas importantes de saber sobre o plano gratuito

- **O Render "dorme" depois de 15 minutos sem uso.** Quando alguém acessa
  depois disso, a primeira resposta demora ~30-60 segundos pra "acordar"
  o servidor. Depois disso fica rápido normalmente. Isso é esperado, não
  é bug.
- **O armazenamento do plano gratuito não é permanente.** Se o Render
  reiniciar o serviço (o que acontece de vez em quando), o banco de dados
  (`kyky.db`, com os usuários) e as memórias de conversa podem ser
  apagados. Pra um teste com amigos tá ótimo; se um dia quiser que os
  dados fiquem salvos de verdade, dá pra conectar um banco externo
  gratuito (ex: um Postgres grátis do próprio Render ou do Supabase).
- **A chave GROQ_API_KEY** fica só no Render, nunca no código que você
  sobe pro GitHub — assim ninguém mais consegue usá-la.

## Administração

Como admin, você tem acesso a:
- `GET /admin/users` — lista todo mundo cadastrado
- `DELETE /admin/users/{username}` — remove alguém

(Não tem tela visual pra isso ainda — dá pra acessar direto pela URL, ex:
`https://seu-link.onrender.com/admin/users`, mandando o header
`Authorization: Bearer SEU_TOKEN`. Se quiser, eu monto um painel visual
depois.)

## Personalizando a Kyky

Abra `main.py` e edite `BASE_PERSONALITY`, `ADMIN_ADDENDUM` e
`USER_ADDENDUM` pra mudar tom, valores e como ela trata você vs. seus
amigos.

## Rodando localmente antes de subir (recomendado)

```bash
pip install -r requirements.txt
export GROQ_API_KEY="sua-chave-aqui"
python main.py
```
Abra `http://localhost:8000`, cadastre-se, teste — só depois suba pro
GitHub/Render.

## Próximos passos possíveis

- Painel visual de administração
- Banco de dados persistente (Postgres gratuito)
- Plugar essa mesma "cabeça" no seu bot de Discord
