"""Optional Computer Use agent for Sparky.

This module is intentionally isolated from the voice, listener and avatar
pipeline. If Computer Use is disabled, the rest of Sparky behaves exactly as it
did before.
"""

import base64
import json
import re
import threading
import time
from copy import deepcopy
from urllib.parse import urlparse

from sparky.config import (
    COMPUTER_USE_ALLOWED_DOMAINS,
    COMPUTER_USE_API_TIMEOUT,
    COMPUTER_USE_CONFIRMATION_TIMEOUT,
    COMPUTER_USE_ENABLED,
    COMPUTER_USE_ENVIRONMENT,
    COMPUTER_USE_HEADLESS,
    COMPUTER_USE_MODEL,
    COMPUTER_USE_REQUIRE_CONFIRMATION,
    COMPUTER_USE_SCREEN_HEIGHT,
    COMPUTER_USE_SCREEN_WIDTH,
    COMPUTER_USE_START_URL,
    COMPUTER_USE_TURN_LIMIT,
    GEMINI_API_KEY,
)


_SENSITIVE_KEYWORDS = (
    "pay", "payment", "checkout", "purchase", "buy", "order",
    "pagar", "pago", "comprar", "pedido",
    "delete", "remove", "archive", "borrar", "eliminar",
    "send email", "send message", "enviar correo", "enviar mensaje",
    "password", "passcode", "2fa", "otp", "contrasena", "codigo",
    "terms", "agreement", "accept", "aceptar terminos",
)


class ComputerAgent:
    """Runs a supervised Gemini Computer Use task in a browser."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread = None
        self._stop = threading.Event()
        self._confirm = threading.Event()
        self._confirm_decision = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._state = {
            "enabled": COMPUTER_USE_ENABLED,
            "status": "idle" if COMPUTER_USE_ENABLED else "disabled",
            "task": "",
            "message": "Computer Use deshabilitado." if not COMPUTER_USE_ENABLED else "Listo.",
            "current_intent": "",
            "last_action": "",
            "last_args": "",
            "current_url": "",
            "steps": 0,
            "awaiting_confirmation": False,
            "confirmation_reason": "",
            "result": "",
            "error": "",
            "updated_at": time.time(),
        }

    def status(self):
        with self._lock:
            return deepcopy(self._state)

    def start_task(self, task):
        task = (task or "").strip()
        if not task:
            return self._with_message("No hay tarea para ejecutar.", error="Tarea vacia.")
        if not COMPUTER_USE_ENABLED:
            return self._with_message("Computer Use esta deshabilitado.", status="disabled")
        if COMPUTER_USE_ENVIRONMENT != "browser":
            return self._with_message(
                "Solo el entorno browser esta implementado en este MVP.",
                status="failed",
                error=f"Entorno no soportado: {COMPUTER_USE_ENVIRONMENT}",
            )
        if not GEMINI_API_KEY:
            return self._with_message(
                "Falta GEMINI_API_KEY para Computer Use.",
                status="failed",
                error="GEMINI_API_KEY vacia.",
            )

        with self._lock:
            if self._state["status"] in ("starting", "running", "waiting_confirmation"):
                return deepcopy(self._state)
            self._stop.clear()
            self._confirm.clear()
            self._confirm_decision = None
            self._state.update({
                "enabled": True,
                "status": "starting",
                "task": task,
                "message": "Preparando navegador supervisado.",
                "current_intent": "",
                "last_action": "",
                "last_args": "",
                "current_url": "",
                "steps": 0,
                "awaiting_confirmation": False,
                "confirmation_reason": "",
                "result": "",
                "error": "",
                "updated_at": time.time(),
            })
            self._thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
            self._thread.start()
            return deepcopy(self._state)

    def confirm(self, approved):
        with self._lock:
            if self._state["status"] != "waiting_confirmation":
                return deepcopy(self._state)
            self._confirm_decision = bool(approved)
            self._state.update({
                "message": "Confirmacion recibida." if approved else "Accion cancelada por el usuario.",
                "updated_at": time.time(),
            })
            self._confirm.set()
            return deepcopy(self._state)

    def stop(self):
        self._stop.set()
        with self._lock:
            if self._state["status"] == "waiting_confirmation":
                self._confirm_decision = False
                self._confirm.set()
            if self._state["status"] in ("starting", "running", "waiting_confirmation"):
                self._state.update({
                    "status": "stopping",
                    "message": "Deteniendo tarea de navegador.",
                    "awaiting_confirmation": False,
                    "confirmation_reason": "",
                    "updated_at": time.time(),
                })
            elif self._state["status"] in ("completed", "failed", "stopped"):
                self._state.update({
                    "status": "idle",
                    "task": "",
                    "message": "Listo.",
                    "current_intent": "",
                    "last_action": "",
                    "last_args": "",
                    "current_url": "",
                    "steps": 0,
                    "awaiting_confirmation": False,
                    "confirmation_reason": "",
                    "result": "",
                    "error": "",
                    "updated_at": time.time(),
                })
            return deepcopy(self._state)

    def _run_task(self, task):
        try:
            try:
                import truststore
                truststore.inject_into_ssl()
            except Exception:
                pass
            from google import genai
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright

            self._update(status="starting", message="Abriendo Chrome supervisado.")
            client = genai.Client(api_key=GEMINI_API_KEY)
            self._start_browser(sync_playwright, PlaywrightError)

            self._page.goto(self._initial_url(task), wait_until="load", timeout=15000)
            self._update(status="running", message="Consultando Gemini Computer Use.",
                         current_url=self._page.url)

            screenshot = self._screenshot_b64()
            interaction = client.interactions.create(
                model=COMPUTER_USE_MODEL,
                input=[
                    {"type": "text", "text": task},
                    {"type": "image", "data": screenshot, "mime_type": "image/png"},
                ],
                tools=[self._tool_config()],
                timeout=COMPUTER_USE_API_TIMEOUT,
            )

            final_text = ""
            for step_index in range(1, COMPUTER_USE_TURN_LIMIT + 1):
                self._raise_if_stopped()
                calls = self._function_calls(interaction)
                self._update(steps=step_index, current_url=self._safe_url())
                if not calls:
                    final_text = self._model_text(interaction) or "Tarea terminada."
                    self._update(status="completed", message="Tarea completada.",
                                 result=final_text, current_intent="", last_action="")
                    break

                results = self._execute_function_calls(calls, PlaywrightTimeoutError)
                function_responses = self._function_responses(results)
                self._raise_if_stopped()
                interaction = client.interactions.create(
                    model=COMPUTER_USE_MODEL,
                    previous_interaction_id=self._get(interaction, "id"),
                    input=function_responses,
                    tools=[self._tool_config()],
                    timeout=COMPUTER_USE_API_TIMEOUT,
                )
            else:
                final_text = "Tarea detenida por limite de pasos."
                self._update(status="completed", message=final_text, result=final_text,
                             current_intent="", last_action="")
        except _Stopped:
            self._update(status="stopped", message="Tarea detenida por el usuario.",
                         awaiting_confirmation=False, confirmation_reason="")
        except Exception as exc:
            self._update(status="failed", message="Computer Use fallo.", error=str(exc),
                         awaiting_confirmation=False, confirmation_reason="")
        finally:
            self._close_browser()

    def _start_browser(self, sync_playwright, playwright_error):
        self._playwright = sync_playwright().start()
        launch_args = {"headless": COMPUTER_USE_HEADLESS}
        try:
            self._browser = self._playwright.chromium.launch(channel="chrome", **launch_args)
        except playwright_error:
            try:
                self._browser = self._playwright.chromium.launch(channel="msedge", **launch_args)
            except playwright_error:
                self._browser = self._playwright.chromium.launch(**launch_args)
        self._context = self._browser.new_context(
            viewport={"width": COMPUTER_USE_SCREEN_WIDTH, "height": COMPUTER_USE_SCREEN_HEIGHT}
        )
        self._page = self._context.new_page()

    def _execute_function_calls(self, calls, timeout_error):
        results = []
        for call in calls:
            self._raise_if_stopped()
            name = self._get(call, "name", "")
            args = self._get(call, "arguments", {}) or {}
            call_id = self._get(call, "id") or self._get(call, "call_id") or name
            intent = args.get("intent", "") if isinstance(args, dict) else ""
            last_args = json.dumps(args, ensure_ascii=False)[:600] if isinstance(args, dict) else str(args)[:600]
            self._update(status="running", current_intent=intent, last_action=name,
                         last_args=last_args, message=f"Ejecutando: {name}",
                         current_url=self._safe_url())
            try:
                reason = self._confirmation_reason(name, args, call)
                safety_acknowledged = False
                if reason:
                    self._wait_for_confirmation(reason)
                    safety_acknowledged = True
                result = self._execute_action(name, args)
                if safety_acknowledged:
                    result["safety_acknowledgement"] = True
                try:
                    self._page.wait_for_load_state("load", timeout=5000)
                except timeout_error:
                    pass
                time.sleep(0.5)
            except Exception as exc:
                result = {"error": str(exc)}
            results.append((name, call_id, result))
        return results

    def _execute_action(self, name, args):
        if name in ("open_web_browser", "open_app"):
            return {"ok": True}
        if name in ("click", "click_at", "double_click", "triple_click",
                    "middle_click", "right_click", "move", "long_press"):
            x, y = self._xy(args)
            if name in ("click", "click_at"):
                self._page.mouse.click(x, y)
            elif name == "double_click":
                self._page.mouse.dblclick(x, y)
            elif name == "triple_click":
                self._page.mouse.click(x, y, click_count=3)
            elif name == "middle_click":
                self._page.mouse.click(x, y, button="middle")
            elif name == "right_click":
                self._page.mouse.click(x, y, button="right")
            elif name == "move":
                self._page.mouse.move(x, y)
            elif name == "long_press":
                self._page.mouse.move(x, y)
                self._page.mouse.down()
                time.sleep(min(float(args.get("seconds", 2)), 5))
                self._page.mouse.up()
            return {"ok": True}
        if name == "hover_at":
            self._page.mouse.move(*self._xy(args))
            return {"ok": True}
        if name in ("mouse_down", "mouse_up"):
            x, y = self._xy(args)
            self._page.mouse.move(x, y)
            if name == "mouse_down":
                self._page.mouse.down()
            else:
                self._page.mouse.up()
            return {"ok": True}
        if name in ("type", "type_text_at"):
            if "x" in args and "y" in args:
                self._page.mouse.click(*self._xy(args))
            text = str(args.get("text", ""))
            self._page.keyboard.press("Control+A")
            self._page.keyboard.press("Backspace")
            self._page.keyboard.type(text)
            intent = str(args.get("intent", "")).lower()
            if args.get("press_enter") or "enter" in intent or "presionar enter" in intent:
                self._page.keyboard.press("Enter")
            return {"ok": True}
        if name == "navigate":
            url = self._normalized_url(str(args.get("url", "")))
            self._ensure_allowed_url(url)
            self._page.goto(url, wait_until="load", timeout=15000)
            return {"ok": True, "url": self._page.url}
        if name == "search":
            self._page.goto(COMPUTER_USE_START_URL, wait_until="load", timeout=15000)
            return {"ok": True, "url": self._page.url}
        if name == "go_back":
            self._page.go_back()
            return {"ok": True}
        if name == "go_forward":
            self._page.go_forward()
            return {"ok": True}
        if name == "wait":
            time.sleep(min(float(args.get("seconds", 1)), 10))
            return {"ok": True}
        if name == "wait_5_seconds":
            time.sleep(5)
            return {"ok": True}
        if name == "press_key":
            self._page.keyboard.press(str(args.get("key", "")))
            return {"ok": True}
        if name == "key_down":
            self._page.keyboard.down(str(args.get("key", "")))
            return {"ok": True}
        if name == "key_up":
            self._page.keyboard.up(str(args.get("key", "")))
            return {"ok": True}
        if name == "hotkey":
            keys = args.get("keys", [])
            if isinstance(keys, str):
                keys = [keys]
            self._page.keyboard.press("+".join(str(k) for k in keys))
            return {"ok": True}
        if name == "key_combination":
            self._page.keyboard.press(str(args.get("keys", "")))
            return {"ok": True}
        if name in ("scroll", "scroll_document", "scroll_at"):
            if "x" in args and "y" in args:
                self._page.mouse.move(*self._xy(args))
            direction = str(args.get("direction", "down")).lower()
            magnitude = int(args.get("magnitude_in_pixels", args.get("magnitude", 300)))
            dx = magnitude if direction == "right" else -magnitude if direction == "left" else 0
            dy = magnitude if direction == "down" else -magnitude if direction == "up" else 0
            self._page.mouse.wheel(dx, dy)
            return {"ok": True}
        if name == "drag_and_drop":
            sx = self._scale_x(args.get("start_x", args.get("x", 0)))
            sy = self._scale_y(args.get("start_y", args.get("y", 0)))
            ex = self._scale_x(args.get("end_x", args.get("destination_x", 0)))
            ey = self._scale_y(args.get("end_y", args.get("destination_y", 0)))
            self._page.mouse.move(sx, sy)
            self._page.mouse.down()
            self._page.mouse.move(ex, ey, steps=10)
            self._page.mouse.up()
            return {"ok": True}
        if name == "take_screenshot":
            return {"ok": True}
        return {"warning": f"Accion no implementada: {name}"}

    def _function_responses(self, results):
        screenshot = self._screenshot_b64()
        current_url = self._safe_url()
        responses = []
        for name, call_id, result in results:
            responses.append({
                "type": "function_result",
                "name": name,
                "call_id": call_id,
                "result": [
                    {
                        "type": "text",
                        "text": json.dumps({"url": current_url, **result}, ensure_ascii=False),
                    },
                    {"type": "image", "data": screenshot, "mime_type": "image/png"},
                ],
            })
        return responses

    def _confirmation_reason(self, name, args, call):
        serialized = json.dumps(self._plain(call), ensure_ascii=False).lower()
        if "blocked" in serialized and "not blocked" not in serialized:
            raise RuntimeError("La politica de seguridad bloqueo esta accion.")
        if "require_confirmation" in serialized:
            return "Gemini marco esta accion como sensible."

        if name == "navigate":
            url = self._normalized_url(str(args.get("url", "")))
            if COMPUTER_USE_ALLOWED_DOMAINS and not self._domain_allowed(url):
                return f"El sitio no esta en la lista permitida: {url}"

        if not COMPUTER_USE_REQUIRE_CONFIRMATION:
            return ""
        text = f"{name} {json.dumps(args, ensure_ascii=False)}".lower()
        for keyword in _SENSITIVE_KEYWORDS:
            if keyword in text:
                return f"Accion sensible detectada: {keyword}"
        return ""

    def _wait_for_confirmation(self, reason):
        self._confirm.clear()
        self._confirm_decision = None
        self._update(status="waiting_confirmation", awaiting_confirmation=True,
                     confirmation_reason=reason, message="Esperando confirmacion del usuario.")
        if not self._confirm.wait(COMPUTER_USE_CONFIRMATION_TIMEOUT):
            raise RuntimeError("Tiempo agotado esperando confirmacion.")
        if not self._confirm_decision:
            raise RuntimeError("Accion cancelada por el usuario.")
        self._update(status="running", awaiting_confirmation=False, confirmation_reason="",
                     message="Confirmacion aprobada.")

    def _tool_config(self):
        return {
            "type": "computer_use",
            "environment": COMPUTER_USE_ENVIRONMENT,
            "enable_prompt_injection_detection": True,
        }

    def _function_calls(self, interaction):
        return [
            step for step in (self._get(interaction, "steps", []) or [])
            if self._get(step, "type") == "function_call"
        ]

    def _model_text(self, interaction):
        parts = []
        for step in self._get(interaction, "steps", []) or []:
            if self._get(step, "type") != "model_output":
                continue
            for block in self._get(step, "content", []) or []:
                if self._get(block, "type") == "text":
                    parts.append(str(self._get(block, "text", "")))
        return " ".join(p for p in parts if p).strip()

    def _screenshot_b64(self):
        data = self._page.screenshot(type="png")
        return base64.b64encode(data).decode("utf-8")

    def _xy(self, args):
        return self._scale_x(args.get("x", 0)), self._scale_y(args.get("y", 0))

    def _scale_x(self, x):
        return int(float(x) / 1000 * COMPUTER_USE_SCREEN_WIDTH)

    def _scale_y(self, y):
        return int(float(y) / 1000 * COMPUTER_USE_SCREEN_HEIGHT)

    def _normalized_url(self, url):
        url = url.strip()
        if not url:
            raise RuntimeError("URL vacia.")
        if not urlparse(url).scheme:
            url = "https://" + url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError(f"Esquema no permitido: {parsed.scheme}")
        return url

    def _initial_url(self, task):
        match = re.search(r"https?://[^\s\"')]+", task or "")
        if match:
            return self._normalized_url(match.group(0).rstrip(".,;"))
        return COMPUTER_USE_START_URL

    def _ensure_allowed_url(self, url):
        if COMPUTER_USE_ALLOWED_DOMAINS and not self._domain_allowed(url):
            raise RuntimeError(f"Dominio no permitido: {urlparse(url).netloc}")

    def _domain_allowed(self, url):
        host = urlparse(url).netloc.lower().split(":")[0]
        for allowed in COMPUTER_USE_ALLOWED_DOMAINS:
            domain = str(allowed).lower()
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def _safe_url(self):
        try:
            return self._page.url if self._page else ""
        except Exception:
            return ""

    def _raise_if_stopped(self):
        if self._stop.is_set():
            raise _Stopped()

    def _close_browser(self):
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def _update(self, **items):
        with self._lock:
            self._state.update(items)
            self._state["enabled"] = COMPUTER_USE_ENABLED
            self._state["updated_at"] = time.time()

    def _with_message(self, message, status=None, error=""):
        with self._lock:
            self._state.update({
                "enabled": COMPUTER_USE_ENABLED,
                "message": message,
                "error": error,
                "updated_at": time.time(),
            })
            if status:
                self._state["status"] = status
            return deepcopy(self._state)

    @staticmethod
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _plain(cls, obj):
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, dict):
            return {k: cls._plain(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [cls._plain(v) for v in obj]
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "to_json_dict"):
            return obj.to_json_dict()
        return str(obj)


class _Stopped(Exception):
    pass
