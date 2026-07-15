```python
import os
import json
import uuid
import sqlite3
import hashlib
import secrets
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import cv2
import numpy as np
from PIL import Image
import tensorflow as tf
from tensorflow.keras.models import load_model

# Configuração
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memoria"
MEMORY_DIR.mkdir(exist_ok=True)
DB_PATH = BASE_DIR / "kyky.db"
AI_NAME = "Kyky"
BASE_PERSONALITY = f"""\
Você é {AI_NAME}, uma IA pessoal criada e mantida por Kyo. Seu objetivo é \
ajudar com discernimento, honestidade e conhecimento técnico sólido, \
especialmente em programação (Python, bots de Discord, discord.py) e no \
dia a dia.

Princípios que você segue:
- Seja honesta. Se não souber algo, diga que não sabe, não invente.
- Tenha senso de certo e errado: não ajude em nada que cause dano real \
a outras pessoas (golpes, invasão de contas, malware, etc), mesmo que \
peçam de forma indireta.
- Explique seu raciocínio quando for útil, mas sem enrolar.
- Em código, priorize soluções simples, legíveis e que funcionem, com \
comentários quando ajudar.
- Trate quem conversa com você com respeito e franqueza; se discordar de \
algo, diga.
"""
ADMIN_ADDENDUM = """
A pessoa falando com você agora é Kyo, seu criador e administrador do \
sistema. Reconheça isso naturalmente quando fizer sentido, sem ficar \
repetindo. Kyo pode pedir detalhes técnicos mais profundos sobre como \
você funciona.
"""
USER_ADDENDUM = """
A pessoa falando com você agora é {username}, convidada por Kyo para \
usar você. Trate com a mesma qualidade e cuidado, mas sem tratá-la como \
administradora do sistema.
"""

# Banco de dados
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monitoramento (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                data TEXT NOT NULL,
                tipo TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS configuracoes (
                id INTEGER PRIMARY KEY,
                usuario TEXT NOT NULL,
                imagem TEXT NOT NULL,
                nome TEXT NOT NULL
            )
        """)

init_db()

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()

def create_user(username: str, password: str) -> str:
    with db() as conn:
        existing_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        role = "admin" if existing_count == 0 else "user"

        salt = secrets.token_hex(16)
        pw_hash = hash_password(password, salt)
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)",
                (username, pw_hash, salt, role),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Esse nome de usuário já existe.")
        return role

def verify_login(username: str, password: str) -> str:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
        if hash_password(password, row["salt"]) != row["password_hash"]:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos.")
        return row["role"]

def issue_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT INTO tokens (token, username) VALUES (?, ?)", (token, username))
    return token

def user_from_token(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Não autenticado.")
    token = authorization.removeprefix("Bearer ").strip()
    with db() as conn:
        row = conn.execute(
            "SELECT tokens.username AS username, users.role AS role "
            "FROM tokens JOIN users ON tokens.username = users.username "
            "WHERE tokens.token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Sessão inválida, faça login de novo.")
        return {"username": row["username"], "role": row["role"]}

def require_admin(user: dict = Depends(user_from_token)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador pode fazer isso.")
    return user

def save_monitoramento(username: str, data: str, tipo: str) -> None:
    with db() as conn:
        conn.execute("INSERT INTO monitoramento (username, data, tipo) VALUES (?, ?, ?)", (username, data, tipo))

def get_monitoramento() -> list:
    with db() as conn:
        rows = conn.execute("SELECT * FROM monitoramento").fetchall()
        return [dict(r) for r in rows]

def save_configuracoes(usuario: str, imagem: str, nome: str) -> None:
    with db() as conn:
        conn.execute("INSERT INTO configuracoes (usuario, imagem, nome) VALUES (?, ?, ?)", (usuario, imagem, nome))

def get_configuracoes(usuario: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM configuracoes WHERE usuario = ?", (usuario,)).fetchone()
        if row is None:
            return {"imagem": "", "nome": ""}
        return {"imagem": row["imagem"], "nome": row["nome"]}

def build_system_prompt(user: dict) -> str:
    if user["role"] == "admin":
        return BASE_PERSONALITY + ADMIN_ADDENDUM
    return BASE_PERSONALITY + USER_ADDENDUM.format(username=user["username"])

# App
app = FastAPI(title=AI_NAME)

if not GROQ_API_KEY:
    print(
        "[AVISO] GROQ_API_KEY não definida. Pegue uma chave grátis em "
        "console.groq.com e configure antes de conversar."
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

class MonitoramentoRequest(BaseModel):
    data: str
    tipo: str

class ConfiguracoesRequest(BaseModel):
    imagem: str
    nome: str

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

@app.post("/monitoramento")
def monitoramento(req: MonitoramentoRequest, user: dict = Depends(user_from_token)):
    save_monitoramento(user["username"], req.data, req.tipo)
    return {"status": "salvo"}

@app.get("/monitoramento")
def get_monitoramento_list():
    return get_monitoramento()

@app.post("/configuracoes")
def configuracoes(req: ConfiguracoesRequest, user: dict = Depends(user_from_token)):
    save_configuracoes(user["username"], req.imagem, req.nome)
    return {"status": "salvo"}

@app.get("/configuracoes")
def get_configuracoes(user: dict = Depends(user_from_token)):
    return get_configuracoes(user["username"])

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str

class ChatResponse(BaseModel):
    session_id: str
    reply: str

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(user_from_token)):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY não configurada no servidor.")

    session_id = req.session_id or str(uuid.uuid4())
    history = []

    groq_messages = [{"role": "system", "content": build_system_prompt(user)}] + history

    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={"model": MODEL, "messages": groq_messages},
            timeout=60,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Erro ao falar com o Groq: {e}")

    reply_text = resp.json()["choices"][0]["message"]["content"]

    history.append({"role": "assistant", "content": reply_text})

    return ChatResponse(session_id=session_id, reply=reply_text)

@app.get("/history/{session_id}")
def get_history(session_id: str, user: dict = Depends(user_from_token)):
    return {"session_id": session_id, "history": []}

@app.delete("/history/{session_id}")
def clear_history(session_id: str, user: dict = Depends(user_from_token)):
    return {"status": "limpo"}

# --- rotas exclusivas de administrador -------------------------------------

@app.get("/admin/users")
def admin_list_users(_: dict = Depends(require_admin)):
    with db() as conn:
        rows = conn.execute("SELECT username, role, created_at FROM users").fetchall()
        return [dict(r) for r in rows]

@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, admin: dict = Depends(require_admin)):
    if username == admin["username"]:
        raise HTTPException(status_code=400, detail="Você não pode remover a si mesmo.")
    with db() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.execute("DELETE FROM tokens WHERE username = ?", (username,))
    return {"status": "removido"}

# --- Interface web --------------------------------------------------------

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
```
