"""Login assistido nos portais das concessionárias.

Abre o portal num navegador no PC onde o hub roda; o usuário entra com usuário, senha e código de verificação, e
aqui capturamos só o token de acesso da sessão. A senha nunca passa pelo hub. Usado pelo botão "Conectar" do painel
e pelo tools/login_portal.py.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PORTALS = {
    "cpfl": "https://servicosonline.cpfl.com.br/agencia-webapp/",
    "aegea": "https://cliente.corsan.com.br/entrar",
}
CPFL_COOKIE_DOMAIN = "cpfl.com.br"
DOMAINS = {"cpfl": CPFL_COOKIE_DOMAIN, "aegea": "corsan.com.br"}
LOGIN_TIMEOUT_S = 600
DIAG_DIR = Path("logs")
# Todas as chaves do sessionStorage e do localStorage, para achar a sessão mesmo se o portal mudar o nome da chave.
STORAGE_JS = """() => [["session", sessionStorage], ["local", localStorage]].flatMap(
  ([onde, s]) => Object.keys(s).map(k => [onde, k, s.getItem(k)]))"""
# Faixa no topo da janela do portal, para não confundir com o Chrome do dia a dia.
FAIXA_JS = """
(() => {
  const pintar = () => {
    if (!document.body || document.getElementById("hub-faixa")) return;
    const d = document.createElement("div");
    d.id = "hub-faixa";
    d.textContent = "Janela do Hub Billing: entre no portal normalmente. Quando o login terminar, esta janela fecha sozinha.";
    d.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#4f8579;color:#fff;" +
      "font:600 14px system-ui,sans-serif;padding:8px 14px;text-align:center;pointer-events:none";
    document.body.appendChild(d);
  };
  document.addEventListener("DOMContentLoaded", pintar);
  setInterval(pintar, 1500);
})();
"""


class LoginTimeout(RuntimeError):
    pass


@dataclass
class Seen:
    """O que a janela do portal trafegou: respostas de token e um resumo das chamadas (sem valores) para diagnóstico."""

    domain: str = CPFL_COOKIE_DOMAIN
    tokens: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def response(self, url: str, method: str, status: int, has_auth: bool, body: Callable[[], Any]) -> None:
        parts = urlsplit(url)
        if self.domain not in parts.netloc:
            return
        line = f"{method} {parts.netloc}{parts.path} {status}{' (com token)' if has_auth else ''}"
        if not self.calls or self.calls[-1] != line:
            self.calls.append(line)
        if status == 200 and parts.path.rstrip("/").endswith("/token"):
            try:
                data = body()
            except Exception:  # resposta sem JSON
                return
            if isinstance(data, dict) and data.get("access_token"):
                self.tokens.append((url, data))


def _storage(page: Any) -> list[tuple[str, str, Any]]:
    entries = []
    for where, key, raw in page.evaluate(STORAGE_JS) or []:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            value = None
        entries.append((where, key, value))
    return entries


def cpfl_capture(page: Any, context: Any, seen: Seen | None = None) -> dict[str, Any] | None:
    """Pega a resposta do /api/token da Agência Virtual CPFL.

    O site antigo guardava em sessionStorage['userSessionStorage']; o novo (cpfl.com.br/agencia) pode usar outra
    chave, então também olha qualquer JSON com access_token no storage e as respostas de /token vistas na rede.
    Só conclui quando a unidade consumidora já foi escolhida (Instalacao), senão a coleta não acha contas.
    """
    data: dict[str, Any] = {}
    if seen and seen.tokens:
        url, token = seen.tokens[-1]
        data.update(token)
        data["api_base"] = url.rstrip("/").rsplit("/", 1)[0]
        parts = urlsplit(page.url if isinstance(getattr(page, "url", None), str) else "")
        if parts.netloc:
            data["origin"], data["referer"] = f"{parts.scheme}://{parts.netloc}", page.url
    stored = sorted((v for _, k, v in _storage(page) if isinstance(v, dict) and v.get("access_token")),
                    key=lambda v: not v.get("Instalacao"))
    if stored:
        data.update(stored[0])
    if not data.get("access_token") or not (data.get("Instalacao") or data.get("ListaInstalacoes")):
        return None
    data["user_agent"] = page.evaluate("() => navigator.userAgent")
    data["cookies"] = {c["name"]: c["value"] for c in context.cookies() if CPFL_COOKIE_DOMAIN in c["domain"]}
    return data


def aegea_capture(page: Any, context: Any, seen: Seen | None = None) -> dict[str, Any] | None:
    """O portal da Corsan usa MSAL com cache no localStorage: pega o access token da API e o refresh token."""
    access: dict[str, Any] | None = None
    refresh: dict[str, Any] | None = None
    for _, _, item in _storage(page):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("credentialType", ""))
        if kind == "AccessToken" and "AguasAPI" in str(item.get("target", "")):
            access = item
        elif kind == "RefreshToken":
            refresh = item
    if access is None:
        return None
    expires = datetime.fromtimestamp(int(access.get("expiresOn") or access.get("expires_on") or 0), UTC)
    return {"access_token": access["secret"], "refresh_token": (refresh or {}).get("secret"),
            "expires_at": expires.isoformat()}


CAPTURE = {"cpfl": cpfl_capture, "aegea": aegea_capture}


def write_diagnostic(provider: str, seen: Seen, storage_keys: list[str], folder: Path = DIAG_DIR) -> Path:
    """Arquivo para quando o login não é reconhecido: nomes das chaves e caminhos chamados, nunca tokens ou senhas."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"login-{provider}.txt"
    lines = [f"Login assistido {provider} em {datetime.now(UTC).isoformat()}", "",
             "Chaves no navegador:", *sorted(set(storage_keys)), "", "Chamadas ao portal:", *seen.calls[-300:]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def capture(provider: str, timeout_s: int = LOGIN_TIMEOUT_S) -> dict[str, Any]:  # pragma: no cover - abre navegador
    from playwright.sync_api import sync_playwright

    seen = Seen(domain=DOMAINS[provider])
    pending: list[Any] = []
    keys: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(locale="pt-BR")
            context.add_init_script(FAIXA_JS)
            context.on("response", lambda r: pending.append(r))  # lidas no laço abaixo, fora do evento
            context.new_page().goto(PORTALS[provider])
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if not browser.is_connected() or not context.pages:
                    raise LoginTimeout("a janela do portal foi fechada antes de concluir o login. "
                                       + _diag_hint(provider, seen, keys))
                while pending:
                    r = pending.pop(0)
                    try:
                        seen.response(r.url, r.request.method, r.status,
                                      "authorization" in r.request.headers, r.json)
                    except Exception:  # resposta de página que já fechou
                        continue
                for p in context.pages:
                    try:
                        keys = sorted({*keys, *(f"{w}:{k}" for w, k, _ in _storage(p))})
                        data = CAPTURE[provider](p, context, seen)
                    except Exception:  # página navegando entre o portal e o login da Microsoft
                        data = None
                    if data:
                        return data
                context.pages[0].wait_for_timeout(2000)
        finally:
            if browser.is_connected():
                browser.close()
    raise LoginTimeout(f"o login não foi concluído em {timeout_s // 60} minutos. " + _diag_hint(provider, seen, keys))


def _diag_hint(provider: str, seen: Seen, keys: list[str]) -> str:
    try:
        path = write_diagnostic(provider, seen, keys)
    except OSError:
        return ""
    return f"Se você já tinha entrado e escolhido a unidade, envie o arquivo {path.resolve()} para análise."


@dataclass
class LoginJob:
    id: str
    provider: str
    distributor: str | None
    status: str = "aguardando"  # aguardando -> conectado | erro
    error: str | None = None


Capture = Callable[[str, int], dict[str, Any]]
OnSuccess = Callable[[str, str | None, dict[str, Any]], None]


class PortalLogins:
    """Logins disparados pelo painel. Cada um roda numa thread, porque o usuário leva minutos para entrar."""

    def __init__(self, on_success: OnSuccess, capture_fn: Capture | None = None,
                 timeout_s: int = LOGIN_TIMEOUT_S) -> None:
        self.on_success = on_success
        self.capture_fn = capture_fn or capture
        self.timeout_s = timeout_s
        self._jobs: dict[str, LoginJob] = {}
        self._lock = threading.Lock()

    def start(self, provider: str, distributor: str | None) -> LoginJob:
        if provider not in PORTALS:
            raise ValueError(f"portal sem login assistido: {provider}")
        with self._lock:
            if any(j.provider == provider and j.status == "aguardando" for j in self._jobs.values()):
                raise RuntimeError("já existe uma janela de login aberta para este portal")
            job = LoginJob(str(uuid.uuid4()), provider, distributor)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"login-{provider}").start()
        return job

    def get(self, job_id: str) -> LoginJob | None:
        return self._jobs.get(job_id)

    def _run(self, job: LoginJob) -> None:
        try:
            data = self.capture_fn(job.provider, self.timeout_s)
            self.on_success(job.provider, job.distributor, data)
            job.status = "conectado"
        except ImportError:
            job.status, job.error = "erro", "navegador não instalado: rode o iniciar.bat"
        except Exception as exc:  # o painel mostra o motivo ao usuário
            job.status, job.error = "erro", str(exc) or exc.__class__.__name__
