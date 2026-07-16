"""
Kyky - assistente pessoal de IA
----------------------------------------------------------
Backend em FastAPI que conversa com a API gratuita do Groq (nuvem,
sem custo, sem cartão de crédito), tem sistema de login (a primeira
pessoa a se cadastrar vira administradora automaticamente), guarda
memória de conversa por usuário (com histórico de sessões), aceita
imagens e arquivos (PDF/texto), permite personalização visual (ícone)
e dá à administradora um painel com estatísticas de uso.

Persistência: usuários, tokens, sessões, sugestões, histórico de
conversas e configuração (nome/personalidade/modelo) ficam salvos no
Postgres do Supabase (via DATABASE_URL), então nada se perde quando o
Render reinicia o serviço.

Recursos de "autoedição" (o que a Kyky pode / não pode alterar sozinha):
  - Ela PODE ajustar o próprio nome e um bloco de "notas de personalidade"
    (tom, preferências, contexto extra) através de uma ferramenta exposta
    só nas conversas com a administradora. Isso é gravado no banco,
    nunca no código-fonte.
  - Ela PODE propor trechos de código como sugestão (fica pendente para
    a administradora revisar e aplicar manualmente). Ela nunca escreve
    nem executa código no servidor sozinha.
  - As regras de segurança e ética em BASE_PERSONALITY são fixas no
    código e não são editáveis por ela, por design.

Pré-requisitos:
  - uma chave de API gratuita do Groq (console.groq.com)
  - um projeto no Supabase, com as tabelas criadas (ver guia de migração)
    e a variável de ambiente DATABASE_URL apontando para ele

Como rodar localmente pra testar:
    1. pip install -r requirements.txt
    2. export GROQ_API_KEY="sua-chave-aqui"
    3. export DATABASE_URL="postgresql://...supabase.co:6543/postgres"
    4. python main.py
    5. Abra http://localhost:8000 no navegador
    6. Cadastre-se primeiro -> você vira admin automaticamente

Como colocar em produção (grátis): veja o README.md.

----------------------------------------------------------------------
ATUALIZAÇÃO (integração com o Agente Local / voz / controle físico do PC)
----------------------------------------------------------------------
Esta versão expande o que o Agente Local (agente_local.py, rodando no PC
físico da Kyo no Windows) pode executar. A allowlist agora é dividida em
duas partes:

  - LOCAL_AGENT_SAFE_ACTIONS: ações que o agente executa IMEDIATAMENTE,
    sem perguntar nada (abrir programas, ler/listar arquivos, rodar
    código, etc). Continua sendo uma allowlist fixa - a Kyky nunca
    inventa uma ação fora dela.
  - LOCAL_AGENT_SENSITIVE_ACTIONS: ações que alteram/destroem algo ou
    mexem no sistema de verdade (deletar, sobrescrever, mover, instalar,
    desligar, comando livre). Essas SEMPRE são marcadas com
    sensitive=true na fila; é o Agente Local quem mostra um popup de
    confirmação na tela física da Kyo antes de rodar - o backend nunca
    executa nada sozinho, só enfileira.

Migração necessária no Postgres (rode uma vez no Supabase):

    ALTER TABLE local_commands ADD COLUMN IF NOT EXISTS sensitive boolean DEFAULT false;

"""

import os
import json
import uuid
import base64
import hashlib
import secrets
import subprocess
import sys
import tempfile
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager

import requests
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
FALLBACK_MODEL = os.environ.get("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")

DATABASE_URL = os.environ.get("DATABASE_URL")

SANDBOX_TIMEOUT_SECONDS = 10
SANDBOX_OUTPUT_LIMIT = 4000

MAX_HISTORY_MESSAGES_SENT = 10
MAX_TOKENS_WITH_TOOLS = 2048
GROQ_MAX_RETRIES = 2

CODE_READ_ALLOWED_EXTENSIONS = {".py", ".md", ".txt", ".json", ".html", ".css", ".js"}
CODE_READ_MAX_CHARS = 12000

# ---------------------------------------------------------------------------
# Agente Local (J.A.R.V.I.S.) - autenticação e allowlist
# ---------------------------------------------------------------------------
# Token separado do login de usuário. É configurado como variável de ambiente
# tanto aqui (no backend na nuvem) quanto no agente_local.py rodando no PC da
# Kyo. Sem ele, o endpoint de polling/resultado do agente não funciona -
# assim, mesmo que alguém descubra a URL do backend, não consegue ler nem
# injetar comandos na fila sem esse segredo.
LOCAL_AGENT_TOKEN = os.environ.get("LOCAL_AGENT_TOKEN")

# Ações "seguras" - o Agente Local executa direto, sem confirmação.
LOCAL_AGENT_SAFE_ACTIONS = {
    "abrir_programa": "Abre um programa/app pelo nome ou caminho (ex: 'chrome', 'vscode', 'spotify', ou um caminho completo .exe).",
    "abrir_arquivo": "Abre um arquivo existente com o programa padrão do Windows (double-click virtual).",
    "abrir_url": "Abre uma URL no navegador padrão (só http/https).",
    "listar_pasta": "Lista os arquivos e subpastas de um diretório (ex: 'C:/Users/kyo/Documents').",
    "ler_arquivo": "Lê e retorna o conteúdo de um arquivo de texto/código já existente no PC.",
    "pesquisar_arquivos": "Procura arquivos por nome/termo dentro de uma pasta (padrão: pasta do usuário).",
    "criar_arquivo": (
        "Cria um arquivo NOVO. Argumento em JSON: "
        '{"caminho": "...", "conteudo": "..."}. Se o arquivo já existir, '
        "vira ação sensível (sobrescrever_arquivo) automaticamente."
    ),
    "executar_codigo": (
        "Executa um trecho de código Python direto no PC físico da Kyo (fora "
        "de sandbox, com acesso real), pra criar e testar apps/scripts de verdade."
    ),
    "status_sistema": "Retorna uso de CPU, memória RAM e disco no momento.",
    "travar_tela": "Bloqueia a tela do Windows (Win+L).",
    "volume_mudo": "Ativa/desativa o mudo do sistema.",
}

# Ações "sensíveis" - SEMPRE ficam marcadas com sensitive=true na fila. O
# Agente Local mostra um popup de confirmação físico na tela da Kyo antes de
# rodar qualquer uma delas, mesmo que a Kyky já tenha "decidido" fazer.
LOCAL_AGENT_SENSITIVE_ACTIONS = {
    "deletar_arquivo": "Apaga um arquivo existente (vai pra lixeira quando possível, não é permanente na hora).",
    "sobrescrever_arquivo": (
        'Sobrescreve o conteúdo de um arquivo já existente. Argumento JSON: '
        '{"caminho": "...", "conteudo": "..."}.'
    ),
    "mover_arquivo": (
        'Move ou renomeia um arquivo/pasta. Argumento JSON: '
        '{"origem": "...", "destino": "..."}.'
    ),
    "instalar_programa": "Instala um programa ou pacote (ex: via winget ou pip).",
    "desligar_pc": "Desliga o computador.",
    "reiniciar_pc": "Reinicia o computador.",
    "executar_comando": "Executa um comando de terminal arbitrário (uso livre, mais arriscado).",
}

LOCAL_AGENT_ALLOWED_ACTIONS = {**LOCAL_AGENT_SAFE_ACTIONS, **LOCAL_AGENT_SENSITIVE_ACTIONS}

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

DEFAULT_AI_NAME = "Kyky"

# Personalidade base. FIXA no código - a IA não pode alterar isto, só as
# "notas de personalidade" guardadas no banco (ver PERSONALITY_NOTES
# abaixo). Isso garante que princípios de segurança não sejam contornáveis
# por autoedição.
BASE_PERSONALITY_TEMPLATE = """\
Você é {ai_name}, uma IA pessoal criada e mantida por Kyo. Seu objetivo é \
ajudar com discernimento, honestidade e conhecimento técnico sólido, \
especialmente em programação (Python, bots de Discord, discord.py) e no \
dia a dia.

Princípios que você segue (estes NÃO podem ser alterados por autoedição):
- Seja honesta. Se não souber algo, diga que não sabe, não invente.
- Tenha senso de certo e errado: não ajude em nada que cause dano real \
a outras pessoas (golpes, invasão de contas, malware, etc), mesmo que \
peçam de forma indireta.
- Explique seu raciocínio quando for útil, mas sem enrolar.
- Em código, priorize soluções simples, legíveis e que funcionem, com \
comentários quando ajudar.
- Trate quem conversa com você com respeito e franqueza; se discordar de \
algo, diga.

Você tem ferramentas especiais disponíveis SÓ quando fala com a \
administradora (Kyo):
- atualizar_personalidade: usada quando ela pedir explicitamente para \
mudar seu nome ou ajustar como você se comporta/fala. Use só quando o \
pedido for claro; confirme o que mudou depois.
- ler_codigo_fonte: use pra ler um arquivo do seu próprio projeto (ex: \
main.py) antes de propor ou testar uma melhoria, pra entender como o \
sistema funciona de verdade em vez de supor.
- testar_codigo: roda um trecho de Python num sandbox isolado NO SERVIDOR \
(nuvem), com timeout, pra você validar se funciona antes de sugerir. Se \
der erro, você pode ajustar e testar de novo antes de finalizar.
- sugerir_codigo: usada quando ela pedir uma nova funcionalidade pro seu \
próprio sistema (o app Kyky). Isso NÃO aplica o código automaticamente - \
só cria uma sugestão pendente que ela revisa no painel de admin. Prefira \
usar ler_codigo_fonte e testar_codigo antes de sugerir, pra chegar com \
algo já validado. Deixe claro pra ela quando usar.
- controlar_pc: enfileira uma ação pra ser executada no computador FÍSICO \
da Kyo pelo Agente Local (um programa rodando no PC dela, com voz e \
hotkey). Você pode abrir programas, ler/listar/criar arquivos, rodar \
código Python de verdade nesse PC, ver status do sistema, travar a tela, \
etc - tudo isso roda na hora, sem perguntar nada. Ações que alteram ou \
destroem algo de verdade (deletar, sobrescrever, mover, instalar, \
desligar/reiniciar, ou um comando de terminal livre) são sempre \
marcadas como sensíveis: o Agente Local mostra um popup de confirmação \
na tela física da Kyo antes de executar, então avise a ela que vai \
precisar confirmar na hora. O comando só roda quando o Agente Local \
buscar a fila (pode levar alguns segundos por causa do polling).
"""

ADMIN_ADDENDUM = """
A pessoa falando com você agora é Kyo, sua criadora e administradora do \
sistema. Reconheça isso naturalmente quando fizer sentido, sem ficar \
repetindo. Kyo pode pedir detalhes técnicos mais profundos sobre como \
você funciona, pedir para você ajustar sua própria personalidade, ou \
sugerir código novo para o seu sistema. Ela também pode estar falando \
com você por voz, através do Agente Local no PC dela ou pelo celular - \
nesses casos suas respostas podem ser lidas em voz alta, então prefira \
frases mais diretas e naturais de se ouvir, sem abusar de listas longas \
ou blocos de código extensos quando a resposta for por voz.
"""

USER_ADDENDUM = """
A pessoa falando com você agora é {username}, convidada por Kyo para \
usar você. Trate com a mesma qualidade e cuidado, mas sem tratá-la como \
administradora do sistema, e sem usar as ferramentas de autoedição nem \
controlar_pc.
"""

DEFAULT_CONFIG = {
    "ai_name": DEFAULT_AI_NAME,
    "personality_notes": "",
    "icon_url": "/static/icon.png",
    "model": MODEL,
}


# ---------------------------------------------------------------------------
# Banco de dados (Postgres / Supabase)
# ---------------------------------------------------------------------------

@contextmanager
def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não configurada.")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Configuração editável (tabela app_config)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT ai_name, personality_notes, icon_url, model FROM app_config WHERE id = 1"
        )
        row = cur.fetchone()
        return dict(row) if row else dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE app_config
            SET ai_name = %s, personality_notes = %s, icon_url = %s, model = %s
            WHERE id = 1
            """,
            (cfg["ai_name"], cfg["personality_notes"], cfg["icon_url"], cfg["model"]),
        )


# ---------------------------------------------------------------------------
# Usuários / tokens
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def create_user(username: str, password: str) -> str:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        existing_count = cur.fetchone()["c"]
        role = "admin" if existing_count == 0 else "user"

        salt = secrets.token_hex(16)
        pw_hash = hash_password(password, salt)
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, salt, role) VALUES (%s, %s, %s, %s)",
                (username, pw_hash, salt, role),
            )
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(status_code=400, detail="Esse nome de usuário já existe.")
        return role


def verify_login(username: str, password: str) -> str:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
        if hash_password(password, row["salt"]) != row["password_hash"]:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
        return row["role"]


def issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO tokens (token, username) VALUES (%s, %s)", (token, username))
    return token


def user_from_token(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Não autenticado.")
    token = authorization.removeprefix("Bearer ").strip()
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT tokens.username AS username, users.role AS role "
            "FROM tokens JOIN users ON tokens.username = users.username "
            "WHERE tokens.token = %s",
            (token,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Sessão inválida, faça login de novo.")
        return {"username": row["username"], "role": row["role"]}


def require_admin(user: dict = Depends(user_from_token)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador pode fazer isso.")
    return user


# ---------------------------------------------------------------------------
# Sessões (metadados: título, timestamps)
# ---------------------------------------------------------------------------

def touch_session(username: str, session_id: str, first_message: str | None, msg_count: int) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sessions WHERE session_id = %s", (session_id,))
        exists = cur.fetchone() is not None
        ts = now_iso()
        if not exists:
            title = (first_message or "Nova conversa").strip().replace("\n", " ")
            title = (title[:42] + "…") if len(title) > 42 else title
            cur.execute(
                "INSERT INTO sessions (session_id, username, title, message_count, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (session_id, username, title or "Nova conversa", msg_count, ts, ts),
            )
        else:
            cur.execute(
                "UPDATE sessions SET message_count = %s, updated_at = %s WHERE session_id = %s",
                (msg_count, ts, session_id),
            )


def list_sessions(username: str) -> list:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT session_id, title, message_count, created_at, updated_at "
            "FROM sessions WHERE username = %s ORDER BY updated_at DESC",
            (username,),
        )
        return [dict(r) for r in cur.fetchall()]


def rename_session(username: str, session_id: str, title: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sessions SET title = %s WHERE session_id = %s AND username = %s",
            (title[:80], session_id, username),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversa não encontrada.")


def delete_session_row(username: str, session_id: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM sessions WHERE session_id = %s AND username = %s",
            (session_id, username),
        )


# ---------------------------------------------------------------------------
# Memória de conversa (tabela memories, uma linha por usuário+sessão)
# ---------------------------------------------------------------------------

def load_history(username: str, session_id: str) -> list:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT history FROM memories WHERE username = %s AND session_id = %s",
            (username, session_id),
        )
        row = cur.fetchone()
        return row["history"] if row else []


def save_history(username: str, session_id: str, history: list) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO memories (username, session_id, history)
            VALUES (%s, %s, %s)
            ON CONFLICT (username, session_id)
            DO UPDATE SET history = EXCLUDED.history
            """,
            (username, session_id, json.dumps(history, ensure_ascii=False)),
        )


def delete_history(username: str, session_id: str) -> None:
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM memories WHERE username = %s AND session_id = %s",
            (username, session_id),
        )


def build_system_prompt(user: dict, cfg: dict) -> str:
    base = BASE_PERSONALITY_TEMPLATE.format(ai_name=cfg["ai_name"])
    if cfg.get("personality_notes"):
        base += f"\nNotas de personalidade adicionadas por autoedição/admin:\n{cfg['personality_notes']}\n"
    if user["role"] == "admin":
        return base + ADMIN_ADDENDUM
    return base + USER_ADDENDUM.format(username=user["username"])


# ---------------------------------------------------------------------------
# Ferramentas (function calling) - autoedição segura + sugestões de código +
# controle do PC físico. Só são oferecidas ao modelo quando quem fala é a
# administradora.
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "atualizar_personalidade",
            "description": (
                "Atualiza o próprio nome ou as notas de personalidade/tom/contexto "
                "guardadas em configuração. NÃO altera regras de segurança, que são "
                "fixas. Use só quando a administradora pedir isso claramente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campo": {
                        "type": "string",
                        "enum": ["nome", "notas"],
                        "description": "'nome' para trocar o próprio nome, 'notas' para "
                        "substituir o bloco de notas de personalidade (tom, preferências, "
                        "contexto extra).",
                    },
                    "valor": {
                        "type": "string",
                        "description": "Novo valor para o campo escolhido.",
                    },
                },
                "required": ["campo", "valor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sugerir_codigo",
            "description": (
                "Cria uma sugestão de código pendente para uma nova funcionalidade "
                "do próprio sistema Kyky. NÃO aplica nem executa nada - só fica "
                "salva para a administradora revisar no painel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Título curto da sugestão."},
                    "descricao": {
                        "type": "string",
                        "description": "Explicação do que o código faz e por quê.",
                    },
                    "codigo": {"type": "string", "description": "O trecho de código sugerido."},
                    "arquivo": {
                        "type": "string",
                        "description": "Nome do arquivo onde isso provavelmente se encaixa (ex: main.py).",
                    },
                },
                "required": ["titulo", "descricao", "codigo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "testar_codigo",
            "description": (
                "Executa um trecho de código Python num ambiente isolado NO SERVIDOR "
                "(nuvem), com timeout, para você mesma validar se funciona antes de "
                "salvar como sugestão. Não tem acesso a GROQ_API_KEY nem DATABASE_URL, "
                "e é interrompido automaticamente após alguns segundos. Diferente de "
                "controlar_pc/executar_codigo, isso NÃO roda no PC físico da Kyo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "codigo": {"type": "string", "description": "O código Python a executar."},
                },
                "required": ["codigo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ler_codigo_fonte",
            "description": (
                "Lê o conteúdo de um arquivo do próprio projeto Kyky (ex: main.py, "
                "static/index.html) para você entender como o sistema funciona antes "
                "de sugerir ou testar mudanças. Só lê arquivos dentro da pasta do "
                "projeto; não tem acesso a nada fora dela, nem a segredos/variáveis "
                "de ambiente. Arquivos grandes vêm truncados."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {
                        "type": "string",
                        "description": (
                            "Caminho relativo do arquivo dentro do projeto, ex: "
                            "'main.py' ou 'static/index.html'."
                        ),
                    },
                },
                "required": ["caminho"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "controlar_pc",
            "description": (
                "Enfileira uma ação pra ser executada no computador FÍSICO da "
                "administradora pelo Agente Local (um programa rodando na máquina "
                "dela, com voz e hotkey). Ações seguras (abrir programa/arquivo/url, "
                "listar/ler/criar/pesquisar arquivos, executar código Python real, "
                "status do sistema, travar tela, mudo) rodam na hora sem perguntar "
                "nada. Ações sensíveis (deletar, sobrescrever, mover, instalar, "
                "desligar/reiniciar, comando de terminal livre) SEMPRE pedem "
                "confirmação num popup na tela física da Kyo antes de rodar - avise "
                "a ela quando usar uma dessas. O comando fica pendente até o Agente "
                "Local buscar e executar (pode levar alguns segundos, por causa do "
                "polling)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "acao": {
                        "type": "string",
                        "enum": sorted(LOCAL_AGENT_ALLOWED_ACTIONS.keys()),
                        "description": "Qual ação executar (ver allowlist de seguras/sensíveis).",
                    },
                    "argumento": {
                        "type": "string",
                        "description": (
                            "Argumento da ação. Pra ações simples é uma string direta "
                            "(ex: nome do programa, caminho, url, código). Pra ações "
                            "que precisam de mais de um campo (criar_arquivo, "
                            "sobrescrever_arquivo, mover_arquivo) é uma string JSON, "
                            "ex: '{\"caminho\": \"C:/x.txt\", \"conteudo\": \"...\"}'. "
                            "Deixe vazio se a ação não precisar de argumento."
                        ),
                    },
                },
                "required": ["acao"],
            },
        },
    },
]


def execute_tool_call(name: str, args: dict, user: dict, cfg: dict) -> tuple[dict, str]:
    """Executa a ferramenta e retorna (config_atualizado, texto_resultado)."""
    if name == "atualizar_personalidade":
        campo = args.get("campo")
        valor = (args.get("valor") or "").strip()
        if campo == "nome" and valor:
            cfg["ai_name"] = valor[:40]
            save_config(cfg)
            return cfg, f"Nome atualizado para '{cfg['ai_name']}'."
        elif campo == "notas":
            cfg["personality_notes"] = valor[:2000]
            save_config(cfg)
            return cfg, "Notas de personalidade atualizadas."
        return cfg, "Campo inválido, nada foi alterado."

    if name == "sugerir_codigo":
        sug_id = str(uuid.uuid4())
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO suggestions (id, username, title, description, code, file_hint, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pendente', %s)",
                (
                    sug_id,
                    user["username"],
                    (args.get("titulo") or "Sugestão sem título")[:120],
                    args.get("descricao") or "",
                    args.get("codigo") or "",
                    args.get("arquivo") or "",
                    now_iso(),
                ),
            )
        return cfg, "Sugestão de código salva no painel de admin para revisão."

    if name == "testar_codigo":
        codigo = args.get("codigo") or ""
        if not codigo.strip():
            return cfg, "Nenhum código fornecido para testar."
        result = run_sandbox_code(codigo)
        resumo = (
            f"Saída (exit code {result['exit_code']}, {result['duration_seconds']}s):\n"
            f"stdout:\n{result['stdout'] or '(vazio)'}\n\n"
            f"stderr:\n{result['stderr'] or '(vazio)'}"
        )
        if result["timed_out"]:
            resumo = f"⏱️ Tempo esgotado ({SANDBOX_TIMEOUT_SECONDS}s).\n" + resumo
        return cfg, resumo

    if name == "ler_codigo_fonte":
        caminho = args.get("caminho") or ""
        return cfg, read_source_file(caminho)

    if name == "controlar_pc":
        acao = args.get("acao")
        argumento = (args.get("argumento") or "").strip()
        if acao not in LOCAL_AGENT_ALLOWED_ACTIONS:
            return cfg, f"Ação '{acao}' não está na allowlist permitida, nada foi enfileirado."
        cmd_id = enqueue_local_command(user["username"], acao, argumento)
        sensitive = acao in LOCAL_AGENT_SENSITIVE_ACTIONS
        aviso_confirmacao = (
            " ⚠️ Essa é uma ação sensível - vai aparecer um popup de confirmação "
            "na tela física da Kyo, e ela precisa confirmar por lá."
            if sensitive
            else ""
        )
        return cfg, (
            f"Comando '{acao}' enfileirado (id {cmd_id}) para o Agente Local."
            f"{aviso_confirmacao} Ele será executado assim que o agente fizer o "
            "próximo polling - pode levar alguns segundos."
        )

    return cfg, "Ferramenta desconhecida."


# ---------------------------------------------------------------------------
# Agente Local - fila de comandos (tabela local_commands no Postgres)
# ---------------------------------------------------------------------------

def enqueue_local_command(username: str, action: str, argument: str) -> str:
    cmd_id = str(uuid.uuid4())
    sensitive = action in LOCAL_AGENT_SENSITIVE_ACTIONS
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO local_commands (id, username, action, argument, status, sensitive, created_at) "
            "VALUES (%s, %s, %s, %s, 'pendente', %s, %s)",
            (cmd_id, username, action, argument, sensitive, now_iso()),
        )
    return cmd_id


def require_local_agent(authorization: str | None = Header(default=None)) -> None:
    """Autenticação separada do login de usuário, usada só pelo Agente Local.
    Compara com secrets.compare_digest pra evitar timing attack no token."""
    if not LOCAL_AGENT_TOKEN:
        raise HTTPException(status_code=500, detail="LOCAL_AGENT_TOKEN não configurado no servidor.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Agente não autenticado.")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, LOCAL_AGENT_TOKEN):
        raise HTTPException(status_code=401, detail="Token do agente inválido.")


def read_source_file(relative_path: str) -> str:
    """Lê um arquivo dentro de BASE_DIR com travas rígidas de segurança:
    - resolve o caminho e confirma que continua DENTRO de BASE_DIR (impede
      '../' ou caminhos absolutos escapando da pasta do projeto);
    - só permite extensões de texto/código conhecidas (nunca .env, .db etc.);
    - nunca lê o próprio arquivo de configuração de segredos;
    - trunca a saída para não estourar o contexto da API."""
    if not relative_path or not relative_path.strip():
        return "Nenhum caminho de arquivo foi informado."

    raw = relative_path.strip().lstrip("/\\")
    if ".." in Path(raw).parts:
        return "Acesso negado: caminhos com '..' não são permitidos."

    candidate = (BASE_DIR / raw).resolve()

    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError:
        return "Acesso negado: esse caminho está fora da pasta do projeto."

    blocked_names = {".env", ".env.local", "kyky.db"}
    if candidate.name in blocked_names or candidate.name.startswith("."):
        return "Acesso negado: este arquivo é reservado e não pode ser lido."

    if not candidate.exists() or not candidate.is_file():
        return f"Arquivo não encontrado: {raw}"

    if candidate.suffix.lower() not in CODE_READ_ALLOWED_EXTENSIONS:
        return f"Tipo de arquivo não permitido para leitura: {candidate.suffix or '(sem extensão)'}"

    try:
        content = candidate.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Erro ao ler o arquivo: {e}"

    truncated = len(content) > CODE_READ_MAX_CHARS
    content = content[:CODE_READ_MAX_CHARS]
    header = f"Arquivo: {raw} ({len(content)} caracteres{' , truncado' if truncated else ''})\n\n"
    return header + content


class GroqRateLimitError(RuntimeError):
    """Levantada quando a Groq responde 429 e as tentativas automáticas
    (GROQ_MAX_RETRIES) já se esgotaram - dá pro chamador decidir cair pra um
    modelo de fallback em vez de simplesmente falhar."""


def call_groq(messages: list, model: str, tools: list | None = None) -> dict:
    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        payload["max_tokens"] = MAX_TOKENS_WITH_TOOLS

    attempt = 0
    while True:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=60,
        )
        if resp.ok:
            return resp.json()

        if resp.status_code == 429 and attempt < GROQ_MAX_RETRIES:
            wait_seconds = 3.0
            match = re.search(r"try again in ([\d.]+)s", resp.text)
            if match:
                wait_seconds = float(match.group(1)) + 0.5
            time.sleep(min(wait_seconds, 20))
            attempt += 1
            continue

        if resp.status_code == 429:
            raise GroqRateLimitError(f"Groq {resp.status_code}: {resp.text}")

        raise RuntimeError(f"Groq {resp.status_code}: {resp.text}")


# ---------------------------------------------------------------------------
# Sandbox — execução isolada de código Python (NO SERVIDOR), com timeout e
# sem segredos. Diferente da execução real no PC físico via Agente Local.
# ---------------------------------------------------------------------------

def run_sandbox_code(code: str, timeout: int = SANDBOX_TIMEOUT_SECONDS) -> dict:
    restricted_env = {
        k: v for k, v in os.environ.items()
        if k not in {"GROQ_API_KEY", "DATABASE_URL", "LOCAL_AGENT_TOKEN"}
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = Path(tmp_dir) / "sandbox_script.py"
        script_path.write_text(code, encoding="utf-8")

        start = datetime.now(timezone.utc)
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=tmp_dir,
                env=restricted_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            return {
                "stdout": result.stdout[:SANDBOX_OUTPUT_LIMIT],
                "stderr": result.stderr[:SANDBOX_OUTPUT_LIMIT],
                "exit_code": result.returncode,
                "timed_out": False,
                "duration_seconds": round(duration, 2),
            }
        except subprocess.TimeoutExpired as e:
            return {
                "stdout": (e.stdout or "")[:SANDBOX_OUTPUT_LIMIT],
                "stderr": (e.stderr or "") + f"\n[Execução interrompida: passou de {timeout}s]",
                "exit_code": None,
                "timed_out": True,
                "duration_seconds": timeout,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": f"Erro ao executar sandbox: {e}",
                "exit_code": None,
                "timed_out": False,
                "duration_seconds": 0,
            }


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title=DEFAULT_AI_NAME)

if not GROQ_API_KEY:
    print(
        "[AVISO] GROQ_API_KEY não definida. Pegue uma chave grátis em "
        "console.groq.com e configure antes de conversar."
    )
if not DATABASE_URL:
    print(
        "[AVISO] DATABASE_URL não definida. Configure a connection string "
        "do Supabase antes de rodar, ou nada será salvo."
    )
if not LOCAL_AGENT_TOKEN:
    print(
        "[AVISO] LOCAL_AGENT_TOKEN não definida. O Agente Local (voz/controle "
        "do PC físico) não vai conseguir se autenticar até você configurar "
        "essa variável (aqui E no .env do agente_local.py, com o MESMO valor)."
    )


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str
    role: str


@app.post("/register", response_model=AuthResponse)
def register(req: RegisterRequest):
    username = req.username.strip()
    if len(username) < 3 or len(req.password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Usuário precisa de 3+ caracteres e senha de 6+ caracteres.",
        )
    role = create_user(username, req.password)
    token = issue_token(username)
    return AuthResponse(token=token, username=username, role=role)


@app.post("/login", response_model=AuthResponse)
def login(req: LoginRequest):
    username = req.username.strip()
    role = verify_login(username, req.password)
    token = issue_token(username)
    return AuthResponse(token=token, username=username, role=role)


@app.get("/me")
def me(user: dict = Depends(user_from_token)):
    return user


@app.get("/config/public")
def config_public():
    cfg = load_config()
    return {"ai_name": cfg["ai_name"], "icon_url": cfg["icon_url"]}


# --- chat -------------------------------------------------------------------

class Attachment(BaseModel):
    type: str
    name: str
    data_url: str | None = None
    extracted_text: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    attachments: list[Attachment] = []
    source: str | None = None  # "web" | "voz" | "mobile" - só informativo por enquanto


class ChatResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(user_from_token)):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY não configurada no servidor.")

    cfg = load_config()
    session_id = req.session_id or str(uuid.uuid4())
    history = load_history(user["username"], session_id)

    text_content = req.message
    image_parts = []
    for att in req.attachments:
        if att.type == "image" and att.data_url:
            image_parts.append({"type": "image_url", "image_url": {"url": att.data_url}})
        elif att.type == "texto" and att.extracted_text:
            trecho = att.extracted_text[:6000]
            text_content += f"\n\n[Conteúdo do arquivo enviado '{att.name}']:\n{trecho}"

    if image_parts:
        user_content = [{"type": "text", "text": text_content or "O que você vê aqui?"}] + image_parts
        model_to_use = VISION_MODEL
    else:
        user_content = text_content
        model_to_use = cfg.get("model", MODEL)

    history.append({
        "role": "user",
        "content": user_content,
        "attachments": [a.model_dump() for a in req.attachments],
    })

    system_prompt = build_system_prompt(user, cfg)
    groq_messages = [{"role": "system", "content": system_prompt}]
    recent_history = history[-MAX_HISTORY_MESSAGES_SENT:]
    for h in recent_history:
        content = h["content"]
        if content is None:
            content = ""
        groq_messages.append({"role": h["role"], "content": content})

    tools = TOOLS_SCHEMA if user["role"] == "admin" else None

    used_fallback = False

    def call_with_fallback(msgs: list) -> dict:
        nonlocal model_to_use, used_fallback
        try:
            return call_groq(msgs, model_to_use, tools)
        except GroqRateLimitError:
            if model_to_use == FALLBACK_MODEL or image_parts:
                raise
            model_to_use = FALLBACK_MODEL
            used_fallback = True
            return call_groq(msgs, model_to_use, tools)

    try:
        data = call_with_fallback(groq_messages)
        msg = data["choices"][0]["message"]

        rounds = 0
        while msg.get("tool_calls") and rounds < 3:
            safe_msg = dict(msg)
            if safe_msg.get("content") is None:
                safe_msg["content"] = ""
            groq_messages.append(safe_msg)

            for call in msg["tool_calls"]:
                fn_name = call["function"]["name"]
                try:
                    fn_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    groq_messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": (
                            "Erro: os argumentos enviados para esta ferramenta não "
                            "vieram em JSON válido (provavelmente o conteúdo era "
                            "grande demais e foi cortado). Tente de novo com um "
                            "trecho de código menor, ou divida a sugestão em partes."
                        ),
                    })
                    continue
                cfg, result_text = execute_tool_call(fn_name, fn_args, user, cfg)
                groq_messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result_text,
                })
            data = call_with_fallback(groq_messages)
            msg = data["choices"][0]["message"]
            rounds += 1

        reply_text = msg.get("content") or "(sem resposta de texto)"
        if used_fallback:
            reply_text += (
                "\n\n_(usei um modelo mais leve por causa de limite de uso "
                "no momento - a resposta pode ser um pouco menos refinada)_"
            )
    except (requests.exceptions.RequestException, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=f"Erro ao falar com o Groq: {e}")

    history.append({"role": "assistant", "content": reply_text, "attachments": []})
    save_history(user["username"], session_id, history)
    touch_session(user["username"], session_id, req.message, len(history))

    return ChatResponse(session_id=session_id, reply=reply_text)


# --- upload de arquivos -------------------------------------------------

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user: dict = Depends(user_from_token)):
    content_type = file.content_type or ""
    raw = await file.read()
    max_bytes = 15 * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(status_code=400, detail="Arquivo maior que 15MB.")

    if content_type.startswith("image/"):
        b64 = base64.b64encode(raw).decode("utf-8")
        return {
            "type": "image",
            "name": file.filename,
            "data_url": f"data:{content_type};base64,{b64}",
        }

    if content_type == "application/pdf" or (file.filename or "").lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            import io

            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Não consegui ler o PDF: {e}")
        return {"type": "texto", "name": file.filename, "extracted_text": text}

    if content_type.startswith("text/") or (file.filename or "").lower().endswith((".txt", ".md", ".csv", ".log")):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Não consegui ler o arquivo: {e}")
        return {"type": "texto", "name": file.filename, "extracted_text": text}

    raise HTTPException(
        status_code=400,
        detail="Tipo de arquivo não suportado (use imagem, PDF ou texto).",
    )


# --- sessões / histórico -------------------------------------------------

@app.get("/sessions")
def get_sessions(user: dict = Depends(user_from_token)):
    return list_sessions(user["username"])


class RenameRequest(BaseModel):
    title: str


@app.patch("/sessions/{session_id}")
def patch_session(session_id: str, req: RenameRequest, user: dict = Depends(user_from_token)):
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Título não pode ser vazio.")
    rename_session(user["username"], session_id, title)
    return {"status": "renomeado"}


@app.get("/history/{session_id}")
def get_history(session_id: str, user: dict = Depends(user_from_token)):
    return {"session_id": session_id, "history": load_history(user["username"], session_id)}


@app.delete("/history/{session_id}")
def clear_history(session_id: str, user: dict = Depends(user_from_token)):
    delete_history(user["username"], session_id)
    delete_session_row(user["username"], session_id)
    return {"status": "limpo"}


# --- rotas exclusivas de administrador -------------------------------------

@app.get("/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT username, role, created_at FROM users")
        return [dict(r) for r in cur.fetchall()]


@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, admin: dict = Depends(require_admin)):
    if username == admin["username"]:
        raise HTTPException(status_code=400, detail="Você não pode remover a si mesmo.")
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE username = %s", (username,))
        cur.execute("DELETE FROM tokens WHERE username = %s", (username,))
        cur.execute("DELETE FROM sessions WHERE username = %s", (username,))
        cur.execute("DELETE FROM memories WHERE username = %s", (username,))
    return {"status": "removido"}


@app.get("/admin/stats")
def admin_stats(_: dict = Depends(require_admin)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        total_users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM sessions")
        total_sessions = cur.fetchone()["c"]
        cur.execute("SELECT COALESCE(SUM(message_count), 0) AS c FROM sessions")
        total_messages = cur.fetchone()["c"]

        now = datetime.now(timezone.utc)
        cutoff_24h = (now - timedelta(hours=24)).isoformat()
        cutoff_7d = (now - timedelta(days=7)).isoformat()

        cur.execute(
            "SELECT COUNT(DISTINCT username) AS c FROM sessions WHERE updated_at >= %s",
            (cutoff_24h,),
        )
        active_24h = cur.fetchone()["c"]
        cur.execute(
            "SELECT COUNT(DISTINCT username) AS c FROM sessions WHERE updated_at >= %s",
            (cutoff_7d,),
        )
        active_7d = cur.fetchone()["c"]

        cur.execute(
            "SELECT substr(CAST(updated_at AS TEXT), 1, 10) AS day, COALESCE(SUM(message_count),0) AS c "
            "FROM sessions GROUP BY day ORDER BY day DESC LIMIT 14"
        )
        rows = cur.fetchall()
        by_day = [{"day": r["day"], "messages": r["c"]} for r in reversed(rows)]

        cur.execute("SELECT COUNT(*) AS c FROM suggestions WHERE status = 'pendente'")
        pending_suggestions = cur.fetchone()["c"]

        return {
            "total_users": total_users,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "messages_by_day": by_day,
            "pending_suggestions": pending_suggestions,
        }


class ConfigUpdateRequest(BaseModel):
    ai_name: str | None = None
    personality_notes: str | None = None
    model: str | None = None


@app.get("/admin/config")
def admin_get_config(_: dict = Depends(require_admin)):
    return load_config()


@app.post("/admin/config")
def admin_update_config(req: ConfigUpdateRequest, _: dict = Depends(require_admin)):
    cfg = load_config()
    if req.ai_name is not None and req.ai_name.strip():
        cfg["ai_name"] = req.ai_name.strip()[:40]
    if req.personality_notes is not None:
        cfg["personality_notes"] = req.personality_notes.strip()[:2000]
    if req.model is not None and req.model.strip():
        cfg["model"] = req.model.strip()
    save_config(cfg)
    return cfg


@app.post("/admin/config/reset")
def admin_reset_config(_: dict = Depends(require_admin)):
    cfg = dict(DEFAULT_CONFIG)
    existing = load_config()
    cfg["icon_url"] = existing.get("icon_url", DEFAULT_CONFIG["icon_url"])
    save_config(cfg)
    return cfg


@app.post("/admin/icon")
async def admin_upload_icon(file: UploadFile = File(...), _: dict = Depends(require_admin)):
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Envie um arquivo de imagem.")
    raw = await file.read()
    if len(raw) > 3 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Ícone deve ter menos de 3MB.")

    ext = ".png"
    if "svg" in content_type:
        ext = ".svg"
    elif "jpeg" in content_type or "jpg" in content_type:
        ext = ".jpg"
    elif "webp" in content_type:
        ext = ".webp"

    icon_path = STATIC_DIR / f"icon{ext}"
    icon_path.write_bytes(raw)

    cfg = load_config()
    cfg["icon_url"] = f"/static/icon{ext}?v={int(datetime.now().timestamp())}"
    save_config(cfg)
    return cfg


@app.get("/admin/suggestions")
def admin_list_suggestions(_: dict = Depends(require_admin)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM suggestions ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]


class SuggestionStatusRequest(BaseModel):
    status: str


@app.post("/admin/suggestions/{sug_id}/status")
def admin_set_suggestion_status(
    sug_id: str, req: SuggestionStatusRequest, _: dict = Depends(require_admin)
):
    if req.status not in ("pendente", "aprovada", "rejeitada"):
        raise HTTPException(status_code=400, detail="Status inválido.")
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE suggestions SET status = %s WHERE id = %s", (req.status, sug_id)
        )
    return {"status": "ok"}


@app.delete("/admin/suggestions/{sug_id}")
def admin_delete_suggestion(sug_id: str, _: dict = Depends(require_admin)):
    with db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM suggestions WHERE id = %s", (sug_id,))
    return {"status": "removido"}


class SandboxRunRequest(BaseModel):
    code: str | None = None
    suggestion_id: str | None = None


@app.post("/admin/sandbox/run")
def admin_run_sandbox(req: SandboxRunRequest, _: dict = Depends(require_admin)):
    code = req.code
    if not code and req.suggestion_id:
        with db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT code FROM suggestions WHERE id = %s", (req.suggestion_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Sugestão não encontrada.")
            code = row["code"]
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="Nenhum código para executar.")
    return run_sandbox_code(code)


class LocalCommandResult(BaseModel):
    status: str  # 'concluido', 'falhou' ou 'recusado' (usuário negou a confirmação)
    result: str


@app.get("/agent/commands/pending")
def agent_pending_commands(_: None = Depends(require_local_agent)):
    """Chamado pelo Agente Local via polling (não pelo navegador/admin).
    Inclui a flag 'sensitive' pra o agente decidir se mostra o popup de
    confirmação antes de executar."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, action, argument, sensitive FROM local_commands "
            "WHERE status = 'pendente' ORDER BY created_at ASC LIMIT 5"
        )
        return [dict(r) for r in cur.fetchall()]


@app.post("/agent/commands/{cmd_id}/result")
def agent_report_result(cmd_id: str, req: LocalCommandResult, _: None = Depends(require_local_agent)):
    """O Agente Local chama isso depois de executar (ou recusar/tentar)."""
    if req.status not in ("concluido", "falhou", "recusado"):
        raise HTTPException(status_code=400, detail="Status inválido.")
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE local_commands SET status = %s, result = %s, completed_at = %s WHERE id = %s",
            (req.status, req.result[:2000], now_iso(), cmd_id),
        )
    return {"status": "ok"}


@app.get("/admin/local/commands")
def admin_list_local_commands(_: dict = Depends(require_admin)):
    """Pro painel admin acompanhar o histórico de comandos enviados ao PC."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, action, argument, sensitive, status, result, created_at, completed_at "
            "FROM local_commands ORDER BY created_at DESC LIMIT 100"
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Interface web
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
