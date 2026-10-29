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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

PORTALS = {
    "cpfl": "https://servicosonline.cpfl.com.br/agencia-webapp/",
    "aegea": "https://cliente.corsan.com.br/entrar",
}
CPFL_COOKIE_DOMAIN = "cpfl.com.br"
LOGIN_TIMEOUT_S = 600
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


def cpfl_capture(page: Any, context: Any) -> dict[str, Any] | None:
    """A Agência Virtual CPFL guarda a resposta inteira do /api/token em sessionStorage['userSessionStorage']."""
    raw = page.evaluate("() => window.sessionStorage.getItem('userSessionStorage')")
    if not raw:
        return None
    data: dict[str, Any] = json.loads(raw)
    if not data.get("access_token"):
        return None
    data["user_agent"] = page.evaluate("() => navigator.userAgent")
    data["cookies"] = {c["name"]: c["value"] for c in context.cookies() if CPFL_COOKIE_DOMAIN in c["domain"]}
    return data


def aegea_capture(page: Any, context: Any) -> dict[str, Any] | None:
    """O portal da Corsan usa MSAL com cache no localStorage: pega o access token da API e o refresh token."""
    entries = page.evaluate("() => Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])")
    access: dict[str, Any] | None = None
    refresh: dict[str, Any] | None = None
    for _, value in entries:
        try:
            item = json.loads(value)
        except (TypeError, ValueError):
            continue
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


def capture(provider: str, timeout_s: int = LOGIN_TIMEOUT_S) -> dict[str, Any]:  # pragma: no cover - abre navegador
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(locale="pt-BR")
            context.add_init_script(FAIXA_JS)
            context.new_page().goto(PORTALS[provider])
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if not browser.is_connected() or not context.pages:
                    raise LoginTimeout("a janela do portal foi fechada antes de concluir o login")
                for p in context.pages:
                    try:
                        data = CAPTURE[provider](p, context)
                    except Exception:  # página navegando entre o portal e o login da Microsoft
                        data = None
                    if data:
                        return data
                time.sleep(2)
        finally:
            if browser.is_connected():
                browser.close()
    raise LoginTimeout(f"o login não foi concluído em {timeout_s // 60} minutos")


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
