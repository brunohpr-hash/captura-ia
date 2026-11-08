# -*- coding: utf-8 -*-
"""
Captura IA — capture qualquer área da tela e deixe a IA trabalhar.
Pressione o atalho (padrão Alt+C), selecione uma região da tela e escolha:
extrair o texto (OCR), explicar o conteúdo ou traduzir para português.
O resultado já vai copiado para a área de transferência.

Feito para Bruno — irmão do Ditado IA, mesma família, mesma chave Groq.
"""

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import keyboard
import pyperclip
import pystray
import requests
from PIL import Image, ImageDraw

APP_NAME = "Captura IA"
APP_VERSION = "1.0.11"
MAX_OUT_TOKENS = 900  # Groq free: OTPM 1000/min; acima disso 429 "Request too large"
UPDATE_REPO = "brunohpr-hash/captura-ia"  # repositório oficial de atualizações
UPDATE_RAW = f"https://raw.githubusercontent.com/{UPDATE_REPO}/main"
NOCACHE = {"Cache-Control": "no-cache", "Pragma": "no-cache"}  # CDN do GitHub segura version.json ~5 min por região
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "CapturaIA"
CONFIG_PATH = APP_DIR / "config.json"
HISTORY_PATH = APP_DIR / "historico.jsonl"
PENDING_DIR = APP_DIR / "pendentes"

DEFAULT_CONFIG = {
    "hotkey": "alt+c",
    "suppress_keys": True,
    "provider": "groq",
    "api_key": "",
    "default_action": "perguntar",  # perguntar | extrair | explicar | traduzir
    "max_side": 1600,  # redimensiona capturas maiores que isso (economia de tokens)
}

PROVIDERS = {
    "groq": {
        "llm_url": "https://api.groq.com/openai/v1/chat/completions",
        "vision_model": "qwen/qwen3.8-27b",
    },
}

ACTION_PROMPTS = {
    "extrair": (
        "Extraia TODO o texto visível nesta imagem, exatamente como está escrito, "
        "preservando a ordem natural de leitura e as quebras de linha. "
        "Se houver tabela, reproduza a estrutura com colunas separadas por ' | '. "
        "Corrija apenas erros óbvios de reconhecimento (letras trocadas), sem mudar o conteúdo. "
        "NÃO adicione comentários, títulos ou explicações. "
        "Responda SOMENTE com o texto extraído."
    ),
    "explicar": (
        "Explique em português do Brasil, de forma clara e objetiva, o que esta imagem mostra. "
        "Se for uma mensagem de erro, diga o que significa e como resolver. "
        "Se for um gráfico ou tabela, resuma os principais números e tendências. "
        "Se for um documento, resuma o que é e os pontos importantes. "
        "Seja direto: comece pela resposta, sem rodeios."
    ),
    "traduzir": (
        "Traduza para português do Brasil todo o texto visível nesta imagem. "
        "Mantenha a estrutura (parágrafos, listas, quebras de linha). "
        "Use linguagem natural, não tradução literal. "
        "Responda SOMENTE com a tradução, sem comentários."
    ),
    "conversa": (
        "Esta imagem mostra uma conversa de mensagens (WhatsApp, Telegram, e-mail ou similar). "
        "Transcreva-a como um diálogo organizado, uma linha em branco entre falas, no formato:\n"
        "[Nome — horário]: mensagem\n\n"
        "Regras:\n"
        "- Identifique cada participante pelo nome que aparece; mensagens alinhadas à direita "
        "(ou destacadas como enviadas) são de [Eu].\n"
        "- Se o horário não estiver visível, omita o '— horário'.\n"
        "- Preserve o texto das mensagens exatamente como está, incluindo os parágrafos "
        "dentro de uma mesma mensagem.\n"
        "- Mantenha a ordem cronológica. Ignore elementos de interface (✓✓, 'digitando...', datas de separação "
        "podem virar uma linha como --- sexta-feira ---).\n"
        "- NÃO resuma, NÃO comente, NÃO responda às mensagens. "
        "Responda SOMENTE com o diálogo transcrito."
    ),
}

ACTION_LABELS = {
    "extrair": "📋 Extrair texto",
    "explicar": "💡 Explicar",
    "traduzir": "🌍 Traduzir",
    "conversa": "💬 Transcrever conversa",
}


# ---------------------------------------------------------------- utilidades

def enable_dpi_awareness():
    """Com escala de tela do Windows (125%, 150%...), as coordenadas do
    seletor (tkinter) e as da captura (ImageGrab) usam 'réguas' diferentes
    e o recorte sai menor/deslocado. Declarar o processo como ciente de DPI
    faz todo mundo usar pixels físicos reais."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
            log("dpi: SetProcessDpiAwareness(2) OK")
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
            log("dpi: SetProcessDPIAware OK (fallback)")
    except Exception as e:
        log(f"dpi: falhou ({e})")



def log(msg):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(APP_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        log(f"erro lendo config: {e}")
    return cfg


def save_config(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(entry):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"erro no histórico: {e}")


def read_history(limit=100):
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(l) for l in lines[-limit:]][::-1]
    except Exception:
        return []


def beep(freq=880, dur=100):
    try:
        import winsound
        winsound.Beep(freq, dur)
    except Exception:
        pass


def image_to_data_uri(img, max_side=1600):
    """Converte a captura em data URI (JPEG) redimensionando se preciso."""
    w, h = img.size
    maior = max(w, h)
    if maior > max_side:
        fator = max_side / maior
        img = img.resize((int(w * fator), int(h * fator)), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


# ---------------------------------------------------------------- medidor de cota

USAGE = {"pct": 0.0}


def update_usage(headers):
    try:
        limit = headers.get("x-ratelimit-limit-requests")
        remaining = headers.get("x-ratelimit-remaining-requests")
        if limit and remaining:
            limit = float(limit)
            if limit > 0:
                USAGE["pct"] = max(0.0, min(100.0, 100.0 * (1 - float(remaining) / limit)))
    except Exception:
        pass


# ---------------------------------------------------------------- pipeline

class Pipeline:
    def __init__(self, cfg):
        self.cfg = cfg

    def analyze(self, img, action):
        """Envia a imagem para o modelo de visão com a instrução da ação."""
        p = PROVIDERS[self.cfg["provider"]]
        prompt = ACTION_PROMPTS.get(action, ACTION_PROMPTS["extrair"])
        data_uri = image_to_data_uri(img, int(self.cfg.get("max_side", 1600)))
        r = requests.post(
            p["llm_url"],
            headers={
                "Authorization": f"Bearer {self.cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": p["vision_model"],
                "temperature": 0.2,
                "reasoning_effort": "none",
                "max_tokens": MAX_OUT_TOKENS,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            },
            timeout=90,
        )
        update_usage(r.headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------- update

UPDATE_FILES = ["main.py", "requirements.txt", "LEIA-ME.md", "iniciar.bat", "instalar.bat"]


_UPDATE_SHA = {"sha": None, "at": 0.0}


def _update_base():
    """URL base imutável do último commit (raw/<sha>/). A CDN do raw.githubusercontent.com cacheia /main por
    ~5 min por região e ignora query string; a API de commits não tem esse cache. Cache local de 60 s."""
    now = time.time()
    if _UPDATE_SHA["sha"] and now - _UPDATE_SHA["at"] < 60:
        return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{_UPDATE_SHA['sha']}"
    try:
        r = requests.get(f"https://api.github.com/repos/{UPDATE_REPO}/commits/main",
                         headers={"Accept": "application/vnd.github+json", **NOCACHE}, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
            if sha:
                _UPDATE_SHA.update(sha=sha, at=now)
                return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{sha}"
        log(f"update: API de commits respondeu {r.status_code}; usando /main (pode estar em cache)")
    except Exception as e:
        log(f"update: falha ao resolver SHA ({e}); usando /main")
    return UPDATE_RAW


def check_remote_version(timeout=10):
    try:
        r = requests.get(f"{_update_base()}/version.json", headers=NOCACHE, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        return d.get("version"), d.get("changelog", "")
    except Exception as e:
        log(f"update check falhou: {e}")
        return None, None


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return (0,)


def download_update(app_dir):
    updated = []
    for fname in UPDATE_FILES:
        try:
            r = requests.get(f"{_update_base()}/{fname}", headers=NOCACHE, timeout=30)
            if r.status_code != 200:
                continue
            dest = app_dir / fname
            tmp = dest.with_suffix(dest.suffix + ".new")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            updated.append(fname)
        except Exception as e:
            log(f"update: falha baixando {fname}: {e}")
    return updated


def restart_app(icon=None):
    """Relança o app de forma deterministica: PRIMEIRO garante o bootstrap
    no ar, DEPOIS derruba o icone/processo. (Corrige corrida em que o
    processo morria antes de lancar o relancador.)"""
    script = Path(__file__).resolve()
    exe = sys.executable
    pyw = Path(exe).with_name("pythonw.exe")
    if pyw.exists():
        exe = str(pyw)
    try:
        if os.name == "nt":
            boot = ("import time,subprocess; time.sleep(3); "
                    f"subprocess.Popen([{exe!r}, {str(script)!r}], "
                    f"cwd={str(script.parent)!r})")
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen([exe, "-c", boot], creationflags=flags, close_fds=True)
            log("restart: bootstrap lançado")
        else:
            subprocess.Popen([exe, str(script)], cwd=str(script.parent))
    except Exception as e:
        log(f"restart falhou: {e}")
    try:
        if icon is not None:
            icon.stop()
    except Exception:
        pass
    os._exit(0)


# ---------------------------------------------------------------- seletor de região

class RegionSelector:
    """Tela cheia escurecida; arraste para selecionar. Esc cancela.
    Retorna (x1, y1, x2, y2) em pixels da tela ou None."""

    def select(self):
        import tkinter as tk

        result = {"bbox": None}
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.25)
        except Exception:
            pass
        root.configure(bg="black", cursor="crosshair")

        canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            root.winfo_screenwidth() // 2, 40,
            text="Arraste para selecionar a área  •  Esc cancela",
            fill="#34d17b", font=("Segoe UI", 14, "bold"))

        start = {"x": 0, "y": 0}
        rect = {"id": None}

        def on_press(e):
            start["x"], start["y"] = e.x_root, e.y_root
            rect["id"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#34d17b", width=3)

        def on_drag(e):
            if rect["id"] is not None:
                canvas.coords(rect["id"],
                              start["x"] - root.winfo_rootx(),
                              start["y"] - root.winfo_rooty(),
                              e.x, e.y)

        def on_release(e):
            x1 = min(start["x"], e.x_root)
            y1 = min(start["y"], e.y_root)
            x2 = max(start["x"], e.x_root)
            y2 = max(start["y"], e.y_root)
            if (x2 - x1) > 8 and (y2 - y1) > 8:
                result["bbox"] = (x1, y1, x2, y2)
            root.destroy()

        def on_escape(_):
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_escape)
        root.focus_force()
        root.mainloop()
        return result["bbox"]


# ---------------------------------------------------------------- app

class CapturaApp:
    def __init__(self):
        self.cfg = load_config()
        self.busy = False
        self._recovering = False
        self._last_key_evt = 0.0  # pulso da escuta global (prova de vida)
        self._probe_until = 0.0  # janela do teste de vida: hotkeys surdos
        self.icon = None
        self._hotkey_handle = None

    # ---------- fluxo principal
    def on_hotkey(self):
        if time.monotonic() < getattr(self, "_probe_until", 0):
            log("atalho ignorado: janela do teste de vida")
            return
        if self.busy:
            log("atalho ignorado: captura anterior ainda em andamento (busy)")
            return
        log("atalho recebido: iniciando captura")
        threading.Thread(target=self._capture_flow, daemon=True).start()

    def _capture_flow(self):
        self.busy = True
        try:
            beep(880, 80)
            bbox = RegionSelector().select()
            if not bbox:
                return

            time.sleep(0.20)  # overlay sumir antes do print
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=bbox, all_screens=True)

            action = self.cfg.get("default_action", "perguntar")
            if action not in ACTION_PROMPTS:
                action = self._ask_action()
                if not action:
                    return

            beep(660, 80)
            self.notify("Analisando a captura...")
            pipe = Pipeline(self.cfg)
            try:
                out = pipe.analyze(img, action)
            except Exception as e:
                body = getattr(getattr(e, "response", None), "text", "")
                log(f"erro API (visao): {e} | {body[:300]}\n{traceback.format_exc()}")
                self._save_pending(img, action)
                self.notify("Sem conexão com a IA. Captura salva em pendentes.")
                beep(300, 300)
                return

            if out:
                try:
                    pyperclip.copy(out)
                except Exception:
                    pass
                beep(990, 90)
                append_history({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "acao": action,
                    "resultado": out,
                })
                self._show_result(ACTION_LABELS.get(action, action), out)
        finally:
            self.busy = False

    def _ask_action(self):
        """Janelinha com as 3 ações. Retorna a escolhida ou None."""
        import tkinter as tk

        choice = {"v": None}
        win = tk.Tk()
        win.title(APP_NAME)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg="#1e1e28")
        # centro da tela
        w, h = 340, 230
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        tk.Label(win, text="O que fazer com a captura?", bg="#1e1e28",
                 fg="#ffffff", font=("Segoe UI", 11, "bold")).pack(pady=(14, 8))

        def pick(a):
            choice["v"] = a
            win.destroy()

        for a in ("extrair", "explicar", "traduzir", "conversa"):
            tk.Button(win, text=ACTION_LABELS[a], width=24,
                      font=("Segoe UI", 10),
                      command=lambda a=a: pick(a)).pack(pady=3)

        win.bind("<Escape>", lambda _: win.destroy())
        win.focus_force()
        win.mainloop()
        return choice["v"]

    def _show_result(self, titulo, texto):
        import tkinter as tk
        from tkinter import ttk

        win = tk.Tk()
        win.title(f"{APP_NAME} — {titulo}")
        win.geometry("640x420")
        win.attributes("-topmost", True)

        tk.Label(win, text="✅ Resultado (já copiado para a área de transferência):",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 4))

        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=10)
        txt = tk.Text(frame, wrap="word", font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=scroll.set)
        txt.insert("1.0", texto)
        txt.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def copiar():
            try:
                pyperclip.copy(txt.get("1.0", "end").strip())
                self.notify("Copiado!")
            except Exception:
                pass

        btns = tk.Frame(win)
        btns.pack(pady=8)
        tk.Button(btns, text="Copiar tudo", width=14, command=copiar).pack(side="left", padx=4)
        tk.Button(btns, text="Fechar", width=14, command=win.destroy).pack(side="left", padx=4)
        win.bind("<Escape>", lambda _: win.destroy())
        win.mainloop()

    # ---------- pendentes
    def _save_pending(self, img, action):
        try:
            PENDING_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            img.save(PENDING_DIR / f"{stamp}_{action}.png")
        except Exception as e:
            log(f"erro salvando pendente: {e}")

    def process_pending(self):
        def run():
            arquivos = sorted(PENDING_DIR.glob("*.png")) if PENDING_DIR.exists() else []
            if not arquivos:
                self.notify("Nenhuma captura pendente.")
                return
            pipe = Pipeline(self.cfg)
            ok = 0
            for f in arquivos:
                try:
                    action = f.stem.split("_")[-1]
                    if action not in ACTION_PROMPTS:
                        action = "extrair"
                    img = Image.open(f)
                    try:
                        out = pipe.analyze(img, action)
                    except Exception as e1:
                        resp = getattr(e1, "response", None)
                        if resp is not None and getattr(resp, "status_code", 0) == 429:
                            log("pendente: limite por minuto (429), aguardando 30s...")
                            time.sleep(30)
                            out = pipe.analyze(img, action)
                        else:
                            raise
                    if out:
                        append_history({
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "acao": action,
                            "resultado": out,
                            "origem": "pendente",
                        })
                        f.unlink()
                        ok += 1
                except Exception as e:
                    log(f"pendente {f.name}: {e}")
            self.notify(f"{ok} captura(s) processada(s). Veja o Histórico.")
        threading.Thread(target=run, daemon=True).start()

    # ---------- hotkey
    def register_hotkey(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        # sentinela de vida: escuta TODO evento de tecla para marcar o pulso
        try:
            if getattr(self, "_pulse_hook", None) is not None:
                try:
                    keyboard.unhook(self._pulse_hook)
                except Exception:
                    pass
            self._pulse_hook = keyboard.on_press(self._pulse)
        except Exception:
            self._pulse_hook = None
        self._hotkey_handle = keyboard.add_hotkey(
            self.cfg["hotkey"], self.on_hotkey,
            suppress=bool(self.cfg.get("suppress_keys", True)),
        )

    # ---------- recuperação pós bloqueio/suspensão (padrão validado no Ditado IA)
    def _recover(self, motivo):
        if self._recovering:
            return
        self._recovering = True
        try:
            time.sleep(3)
            espera = 0
            while self.busy and espera < 120:
                time.sleep(1)
                espera += 1
            log(f"recuperacao: reiniciando o app apos {motivo}")
            restart_app(self.icon)
        except Exception as e:
            log(f"recuperacao falhou ({motivo}): {e}")
            self._recovering = False

    def _session_listener(self):
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            wtsapi = ctypes.windll.wtsapi32

            WM_WTSSESSION_CHANGE = 0x02B1
            WTS_SESSION_UNLOCK = 0x8
            WM_POWERBROADCAST = 0x218
            PBT_APMRESUMESUSPEND = 0x7
            PBT_APMRESUMEAUTOMATIC = 0x12
            NOTIFY_FOR_THIS_SESSION = 0

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM)

            def wnd_proc(hwnd, msg, wparam, lparam):
                try:
                    if msg == WM_WTSSESSION_CHANGE and wparam == WTS_SESSION_UNLOCK:
                        log("evento do Windows: sessao desbloqueada")
                        threading.Thread(target=self._recover,
                                         args=("desbloqueio de sessao",),
                                         daemon=True).start()
                    elif msg == WM_POWERBROADCAST and wparam in (
                            PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                        log("evento do Windows: retomada da suspensao")
                        threading.Thread(target=self._recover,
                                         args=("retomada da suspensao",),
                                         daemon=True).start()
                except Exception as e:
                    log(f"session_listener wnd_proc: {e}")
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            proc = WNDPROC(wnd_proc)
            self._session_wndproc = proc

            class WNDCLASS(ctypes.Structure):
                _fields_ = [
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASS()
            wc.style = 0
            wc.lpfnWndProc = proc
            wc.hInstance = hinst
            wc.lpszClassName = "CapturaIASessionWatcher"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                log(f"session_listener: RegisterClass falhou ({ctypes.GetLastError()})")
                return

            hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "CapturaIA Watcher", 0,
                0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                log(f"session_listener: CreateWindow falhou ({ctypes.GetLastError()})")
                return

            if not wtsapi.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
                log(f"session_listener: WTSRegister falhou ({ctypes.GetLastError()})")
            else:
                log("session_listener: registrado para eventos de sessao do Windows")

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            log(f"session_listener: erro fatal: {e}")

    def _pulse(self, _evt=None):
        # chamado em QUALQUER evento de teclado — batimento cardíaco da escuta
        self._last_key_evt = time.monotonic()

    def _rehook_patrol(self):
        """Ronda com prova de vida: religa o atalho a cada 2 min e TESTA a
        escuta injetando tecla fantasma (F24). Religar nao basta se o motor
        de escuta morreu (caso de campo Ditado 04/11/2026) — 2 falhas
        seguidas confirmam gancho morto e o app se reinicia sozinho."""
        falhas = 0
        while True:
            time.sleep(120)
            try:
                if self.busy or self._recovering:
                    continue
                # limpa teclas "presas" na memoria da lib (apertar registrado
                # sem o soltar — re-registro no meio de um atalho faz isso).
                # Estado preso + evento fantasma = atalho re-disparando sozinho
                # (caso de campo 04/11/2026: Ata iniciando/encerrando reunioes
                # em loop a cada ronda).
                try:
                    keyboard._pressed_events.clear()
                except Exception:
                    pass
                self.register_hotkey()
                # imunidade: enquanto o teste roda, os atalhos do app ficam
                # surdos — a tecla fantasma JAMAIS pode virar acao real
                self._probe_until = time.monotonic() + 2.0
                t0 = time.monotonic()
                injetou = False
                if os.name == "nt":
                    # injecao direta no Windows (VK_F24=0x87): nao depende do
                    # mapeamento de nomes da lib keyboard, que pode falhar em
                    # silencio p/ f24 (tempestade de reinicios de 04/11/2026)
                    try:
                        import ctypes
                        ctypes.windll.user32.keybd_event(0x87, 0, 0, 0)
                        ctypes.windll.user32.keybd_event(0x87, 0, 2, 0)
                        injetou = True
                    except Exception:
                        pass
                if not injetou:
                    try:
                        keyboard.press_and_release("f24")
                        injetou = True
                    except Exception:
                        pass
                time.sleep(1.0)
                if not injetou:
                    # sem injecao comprovada NAO ha teste valido — jamais
                    # contar como falha (era isso que reiniciava os 4 apps)
                    log("ronda: nao consegui injetar a tecla de teste — verificacao pulada")
                    continue
                if self._last_key_evt >= t0:
                    falhas = 0
                    log("ronda: atalho religado e escuta VERIFICADA viva")
                else:
                    falhas += 1
                    log(f"ronda: escuta NAO respondeu ao teste ({falhas}x)")
                    if falhas >= 2:
                        log("ronda: gancho morto confirmado — reiniciando o app")
                        falhas = 0
                        try:
                            self.notify("Escuta do teclado travou — reiniciando sozinho…")
                        except Exception:
                            pass
                        restart_app(self.icon)
                        return
            except Exception as e:
                log(f"ronda: falha ao religar ({e})")

    def _watchdog(self):
        intervalo = 15
        ultimo = time.monotonic()
        while True:
            time.sleep(intervalo)
            agora = time.monotonic()
            saltou = (agora - ultimo) > (intervalo + 25)
            ultimo = agora
            if saltou and not self._recovering:
                log("watchdog reserva: salto de relogio detectado (suspensao)")
                threading.Thread(target=self._recover,
                                 args=("suspensao (reserva)",),
                                 daemon=True).start()

    # ---------- atualização
    def do_update_check(self, silent=False):
        remote, changelog = check_remote_version()
        if not remote:
            if not silent:
                self.notify("Não foi possível verificar atualizações (sem internet?).")
            return
        if version_tuple(remote) <= version_tuple(APP_VERSION):
            if not silent:
                self.notify(f"Você já está na versão mais recente ({APP_VERSION}).")
            return

        def ask_and_apply():
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            msg = (f"Nova versão disponível: {remote} (atual: {APP_VERSION})\n\n"
                   f"Novidades:\n{changelog or '(sem descrição)'}\n\n"
                   "Atualizar agora? O app reinicia sozinho.")
            ok = messagebox.askyesno(f"{APP_NAME} — Atualização", msg, parent=root)
            root.destroy()
            if not ok:
                return
            app_dir = Path(__file__).resolve().parent
            updated = download_update(app_dir)
            if "main.py" in updated:
                log(f"update: v{APP_VERSION} -> v{remote} ({', '.join(updated)})")
                self.notify(f"Atualizado para v{remote}! Reiniciando...")
                time.sleep(1.5)
                restart_app(self.icon)
            else:
                self.notify("Falha ao baixar a atualização. Veja o app.log.")

        threading.Thread(target=ask_and_apply, daemon=True).start()

    # ---------- janelas
    def open_settings(self):
        threading.Thread(target=self._settings_window, daemon=True).start()

    def _settings_window(self):
        import tkinter as tk
        from tkinter import ttk

        win = tk.Tk()
        win.title(f"{APP_NAME} — Configurações")
        win.geometry("520x330")
        win.attributes("-topmost", True)

        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Atalho de captura:").grid(row=0, column=0, sticky="w")
        hot_var = tk.StringVar(master=win, value=self.cfg["hotkey"])
        ttk.Entry(frm, textvariable=hot_var, width=20).grid(row=0, column=1, sticky="w")

        def detect():
            hot_var.set("pressione as teclas...")
            win.update()
            try:
                combo = keyboard.read_hotkey(suppress=False)
                hot_var.set(combo)
            except Exception:
                hot_var.set(self.cfg["hotkey"])

        ttk.Button(frm, text="Detectar", command=detect).grid(row=0, column=2, padx=6)

        ttk.Label(frm, text="Ação padrão:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        act_var = tk.StringVar(master=win, value=self.cfg.get("default_action", "perguntar"))
        ttk.Combobox(frm, textvariable=act_var,
                     values=["perguntar", "extrair", "explicar", "traduzir", "conversa"],
                     state="readonly", width=18).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(frm, text="perguntar = mostra o menu",
                  foreground="#666").grid(row=1, column=2, sticky="w", pady=(8, 0))

        ttk.Label(frm, text="Chave de API (Groq):").grid(row=2, column=0, sticky="w", pady=(8, 0))
        key_var = tk.StringVar(master=win, value=self.cfg["api_key"])
        ttk.Entry(frm, textvariable=key_var, width=38, show="*").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(8, 0))

        sup_var = tk.BooleanVar(master=win, value=self.cfg.get("suppress_keys", True))
        ttk.Checkbutton(frm, text="Bloquear teclas do atalho",
                        variable=sup_var).grid(row=3, column=0, columnspan=3,
                                               sticky="w", pady=(10, 0))

        info = (f"Versão instalada: {APP_VERSION}\n"
                "Pode usar a MESMA chave do Ditado IA (console.groq.com).\n"
                "A chave fica salva somente neste computador.")
        ttk.Label(frm, text=info, foreground="#666").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Button(frm, text="🔄 Verificar atualizações",
                   command=lambda: self.do_update_check(silent=False)).grid(
            row=5, column=0, columnspan=3, pady=(10, 0))

        def salvar():
            self.cfg["hotkey"] = hot_var.get().strip() or "alt+c"
            self.cfg["default_action"] = act_var.get() or "perguntar"
            self.cfg["suppress_keys"] = sup_var.get()
            nova_chave = key_var.get().strip()
            if nova_chave:
                self.cfg["api_key"] = nova_chave
            save_config(self.cfg)
            self.register_hotkey()
            win.destroy()
            self.notify(f"Salvo! Atalho atual: {self.cfg['hotkey']}")

        ttk.Button(frm, text="Salvar", command=salvar).grid(
            row=6, column=0, columnspan=3, pady=14)

        win.mainloop()

    def open_history(self):
        threading.Thread(target=self._history_window, daemon=True).start()

    def _history_window(self):
        import tkinter as tk
        from tkinter import ttk

        win = tk.Tk()
        win.title(f"{APP_NAME} — Histórico")
        win.geometry("700x420")
        win.attributes("-topmost", True)

        cols = ("quando", "acao", "resultado")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("quando", text="Quando")
        tree.heading("acao", text="Ação")
        tree.heading("resultado", text="Resultado (duplo clique copia)")
        tree.column("quando", width=130)
        tree.column("acao", width=80)
        tree.column("resultado", width=460)
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        dados = read_history()
        for d in dados:
            resumo = (d.get("resultado", "") or "").replace("\n", " ")[:120]
            tree.insert("", "end", values=(
                d.get("ts", ""), d.get("acao", ""), resumo))

        def copiar(_):
            sel = tree.selection()
            if sel:
                idx = tree.index(sel[0])
                try:
                    pyperclip.copy(dados[idx].get("resultado", ""))
                    self.notify("Copiado!")
                except Exception:
                    pass

        tree.bind("<Double-1>", copiar)
        win.mainloop()

    # ---------- bandeja
    def notify(self, msg):
        try:
            if self.icon:
                self.icon.notify(msg, APP_NAME)
        except Exception:
            pass

    def _make_icon_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # câmera verde
        d.rounded_rectangle([6, 18, 58, 52], radius=8, fill=(30, 30, 40, 255))
        d.rectangle([22, 12, 42, 20], fill=(30, 30, 40, 255))
        d.ellipse([22, 25, 42, 45], fill=(52, 209, 123, 255))
        d.ellipse([28, 31, 36, 39], fill=(30, 30, 40, 255))
        return img

    def run_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem(f"Atalho: {self.cfg['hotkey'].upper()} e selecione a área",
                             None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Capturar agora", lambda: self.on_hotkey()),
            pystray.MenuItem("Histórico", lambda: self.open_history()),
            pystray.MenuItem("Processar pendentes", lambda: self.process_pending()),
            pystray.MenuItem("Configurações", lambda: self.open_settings()),
            pystray.MenuItem("Verificar atualizações",
                             lambda: self.do_update_check(silent=False)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: self.icon.stop()),
        )
        self.icon = pystray.Icon(APP_NAME, self._make_icon_image(), APP_NAME, menu)
        self.icon.run()

    def run(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        enable_dpi_awareness()
        if not self.cfg.get("api_key"):
            self.open_settings()

        self.register_hotkey()

        # checagem silenciosa de atualização ao iniciar
        threading.Timer(8.0, lambda: self.do_update_check(silent=True)).start()

        # recuperação pós bloqueio/suspensão: canal oficial + reserva
        threading.Thread(target=self._session_listener, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        threading.Thread(target=self._rehook_patrol, daemon=True).start()

        self.run_tray()


if __name__ == "__main__":
    try:
        CapturaApp().run()
    except Exception as e:
        log(f"fatal: {e}\n{traceback.format_exc()}")
        raise
