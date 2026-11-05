"""AI가 조작하는 검사 브라우저. 허용 범위·증거 수집·판정을 모델과 분리해 맡는다.

모델 드라이버(Agent SDK 도구, 공식 Computer Use)는 이 클래스의 동작만 호출한다.
검사 문자열은 등록 템플릿을 여기서 입력하고, 판정은 브라우저에서 관측한 증거로만 내린다.
"""

import asyncio
import base64
from contextlib import suppress
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

from playwright.async_api import async_playwright

from .contracts import Finding
from .registry import ResolvedTarget
from .runner import browser_executable


VIEWPORT = {"width": 960, "height": 640}
MAX_ACTIONS = 40
# 시연에서 클릭 결과를 눈으로 따라갈 수 있도록 클릭마다 기다린다.
CLICK_DELAY = float(os.environ.get("DAST_CLICK_DELAY", "1"))
CURSOR_GLIDE = 0.6
FRAME_INTERVAL = 0.08
PROBE_TYPE_DELAY_MS = int(os.environ.get("DAST_PROBE_TYPE_DELAY_MS", "6"))
# 영향 시연 페이로드(1천 자 넘음)를 입력하는 데 쓰는 시간. 한 글자씩이면 10초 넘게 걸려 덩어리로 빠르게 채운다.
IMPACT_TYPE_SECONDS = float(os.environ.get("DAST_IMPACT_TYPE_SECONDS", "1.5"))
# 취약점이 트리거된 순간에는 실제 효과를 보여주도록 조금 더 머문다.
EXECUTION_HOLD = float(os.environ.get("DAST_TRIGGER_DELAY", "2"))
# 바로 검사는 탐지 직후 바로 끝난다. 블루스크린이 100%(약 3.75초)까지 차서 그대로 멈춘 화면이 마지막 프레임·증거로 남게 머문다.
DIRECT_HOLD = float(os.environ.get("DAST_DIRECT_HOLD", "4"))
CANARY_DIR = Path(__file__).with_name("canaries")
# 반사 위치별로 시도할 카나리. 이스케이프된 출력은 음성 확인용으로 하나만 시도한다.
CANARIES_BY_CONTEXT = {
    "html_unescaped": ["svg_onload", "img_onerror"],
    "attribute": ["attr_break"],
    "script": ["js_string_break"],
    "html_escaped": ["svg_onload"],
}
MAX_CANARIES = 2
LOCATE_MARKER = """m => {
  const found = new Set();
  if ([...document.querySelectorAll('b')].some(b => b.textContent === m)) found.add('html_unescaped');
  if (document.body && document.body.textContent.includes('<b>' + m)) found.add('html_escaped');
  for (const el of document.querySelectorAll('*')) {
    if (el.tagName === 'SCRIPT') { if (el.textContent.includes(m)) found.add('script'); continue; }
    for (const a of el.attributes) if (a.value.includes(m)) found.add('attribute');
  }
  return [...found];
}"""


ID_SEGMENT = re.compile(r"^(?:[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{4,})$", re.I)


def masked_url(url: str) -> str:
    """보고서용 전체 URL. 쿼리 값은 가리고 이름만 남긴다."""
    parsed = urlsplit(url)
    query = "&".join(f"{key}=…" for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" + (f"?{query}" if query else "")


def url_pattern(url: str) -> str:
    """ID처럼 보이는 경로 조각을 {id}로 묶는다."""
    parsed = urlsplit(url)
    path = "/".join("{id}" if ID_SEGMENT.match(part) else part for part in parsed.path.split("/"))
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def load_canaries() -> dict[str, str]:
    return {path.stem: path.read_text().strip() for path in sorted(CANARY_DIR.glob("*.html"))}


HTML_LIKE = re.compile(r"[<>]|javascript:|on\w+\s*=", re.IGNORECASE)


class ActionError(Exception):
    """모델에 되돌려 줄 수 있는 조작 오류."""


class ProbeBrowser:
    def __init__(self, finding: Finding, target: ResolvedTarget, payload_id: str,
                 payload_template: str, run_id: str, output: Path, emit, mode: str):
        self.finding, self.target, self.payload_id = finding, target, payload_id
        self.run_id, self.output, self.emit = run_id, output, emit
        self.nonce = secrets.token_hex(16)
        self.direct = False  # 바로 검사: 반사 확인 없이 영향 시연 페이로드를 넣는다(agent.py DIRECT_PROMPT)
        # payload_template은 취약 확정 뒤에만 쓰는 영향 시연용이다. 탐지는 카나리로 한다.
        self.probe = payload_template.replace("{{DAST_NONCE}}", self.nonce)
        self.canaries = {key: value.replace("{{DAST_NONCE}}", self.nonce)
                         for key, value in load_canaries().items()}
        self.marker_id = secrets.token_hex(6)
        self.marker = f"dastref\"'<b>{self.marker_id}</b>"
        self.phase = "explore"
        self.impact_enabled = True
        self.started = time.monotonic()
        self.session = 1
        self.page = None
        self.context = None
        self.browser = None
        self.done = None
        self.mouse = (VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)
        self._playwright = None
        self._frames = None
        self._shots = 0
        self._cast_at = 0.0
        self.evidence = {
            "nonce": self.nonce, "mode": mode, "target_id": target.target_id,
            "payload_id": payload_id, "actions": [], "screenshots": [], "console": [],
            "probe_typed": False, "sessions": 1, "probe_session": None,
            "executed": [], "rendered": [], "csp_blocked": False, "blocked_requests": 0,
            "marker_typed": False, "reflection": [], "canaries": [], "impact": None,
            "input_points": [],
        }

    # ---- 수명 주기 -------------------------------------------------------
    async def start(self):
        self._playwright = await async_playwright().start()
        executable = browser_executable(self._playwright)
        if not executable:
            raise RuntimeError("사용 가능한 Chrome 또는 Chromium 실행 파일이 없습니다.")
        self.browser = await self._playwright.chromium.launch(
            executable_path=executable, headless=True,
            args=["--disable-background-networking", "--disable-component-update"],
        )
        await self._open_session()
        self._frames = asyncio.create_task(self._stream_frames())

    async def close(self):
        if self._frames:
            self._frames.cancel()
            with suppress(asyncio.CancelledError):
                await self._frames
        with suppress(Exception):
            if self.browser:
                await self.browser.close()
        with suppress(Exception):
            if self._playwright:
                await self._playwright.stop()

    async def _screencast(self, page):
        """Chrome 화면 스트림(CDP screencast)으로 화면이 바뀔 때마다 프레임을 보낸다."""
        try:
            cdp = await page.context.new_cdp_session(page)
        except Exception:
            return

        async def on_frame(params):
            with suppress(Exception):
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            now = time.monotonic()
            if page is self.page and now - self._cast_at >= FRAME_INTERVAL:
                self._cast_at = now
                await self.emit({"type": "frame", "image": "data:image/jpeg;base64," + params["data"], **VIEWPORT})

        cdp.on("Page.screencastFrame", lambda params: asyncio.create_task(on_frame(params)))
        with suppress(Exception):
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 55, "everyNthFrame": 1,
                                                    "maxWidth": VIEWPORT["width"], "maxHeight": VIEWPORT["height"]})

    async def _stream_frames(self):
        # 화면 스트림이 멈춰 있을 때(변화 없음·실패)만 주기적으로 캡처해 마지막 화면을 유지한다.
        while True:
            tick = time.monotonic()
            page = self.page
            if page is not None and not page.is_closed() and tick - self._cast_at > 1.0:
                with suppress(Exception):
                    jpeg = await page.screenshot(type="jpeg", quality=50, timeout=1800)
                    await self.emit({"type": "frame", "image": "data:image/jpeg;base64," +
                                     base64.b64encode(jpeg).decode(), **VIEWPORT})
            await asyncio.sleep(max(0.05, 0.5 - (time.monotonic() - tick)))

    def _note_input(self, request):
        """검사 문자열이 실려 나간 요청의 메서드·URL·파라미터 이름을 기록한다(값은 기록하지 않음)."""
        if not (self.evidence["marker_typed"] or self.evidence["probe_typed"]):
            return
        try:
            body = request.post_data or ""
        except Exception:
            body = ""
        fields = parse_qsl(body, keep_blank_values=True) + parse_qsl(urlsplit(request.url).query, keep_blank_values=True)
        hits = {}
        for key, value in fields:
            if self.nonce in value:
                hits[key] = "probe"
            elif self.marker_id in value:
                hits.setdefault(key, "marker")
        if not hits:
            return
        point = {"method": request.method, "url": masked_url(request.url), "pattern": url_pattern(request.url),
                 "params": sorted(hits), "kind": "probe" if "probe" in hits.values() else "marker",
                 "session": self.session}
        if point not in self.evidence["input_points"]:
            self.evidence["input_points"].append(point)

    def _allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (f"{parsed.scheme}://{parsed.netloc}" == self.target.origin
                and self.target.permits(parsed.path))

    async def _open_session(self):
        if self.context is not None:
            with suppress(Exception):
                await self.context.close()
        context = await self.browser.new_context(viewport=VIEWPORT, service_workers="block",
                                                 accept_downloads=False)
        session = self.session

        async def guard(route):
            request = route.request
            if not self._allowed(request.url):
                self.evidence["blocked_requests"] += 1
                await route.abort("blockedbyclient")
                return
            self._note_input(request)
            from .cloud_run import authorization

            auth = await asyncio.to_thread(authorization, self.target.origin)
            response = await route.fetch(max_redirects=0, timeout=10000,
                                         headers={**request.headers, **auth})
            location = response.headers.get("location")
            # 범위 안의 redirect만 전달한다. 브라우저의 후속 요청은 이 guard를 다시 지난다.
            if 300 <= response.status < 400 and (
                    not location or not self._allowed(urljoin(request.url, location))):
                self.evidence["blocked_requests"] += 1
                await route.abort("blockedbyclient")
            else:
                await route.fulfill(response=response)

        def console(message):
            text = message.text
            if text == "DAST_PROOF:" + self.nonce:
                self.evidence["executed"].append({"session": session, "url": urlsplit(page.url).path,
                                                  "full_url": masked_url(page.url),
                                                  "pattern": url_pattern(page.url), "phase": self.phase})
                asyncio.create_task(self.emit({
                    "type": "execution", "session": session,
                    "message": "검사 문자열이 브라우저에서 실행됐습니다.",
                }))
            elif "content security policy" in text.lower() and self.evidence["probe_typed"]:
                self.evidence["csp_blocked"] = True
                self.evidence["console"].append(text[:600])

        await context.route("**/*", guard)
        await context.route_web_socket("**/*", lambda socket: socket.close())
        page = await context.new_page()
        page.set_default_timeout(7000)
        page.set_default_navigation_timeout(10000)
        context.on("page", lambda extra: asyncio.create_task(extra.close()))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        page.on("console", console)
        self.context, self.page = context, page
        await self._screencast(page)
        await page.goto(self.target.origin + self.target.entry_path, wait_until="load")

    # ---- 관측 ------------------------------------------------------------
    async def _settle(self):
        executed_before = len(self.evidence["executed"])
        with suppress(Exception):
            await self.page.wait_for_load_state("load", timeout=4000)
        await asyncio.sleep(0.4)
        # 입력란 값은 innerHTML에 없으므로, 본문에 nonce가 있으면 서버가 저장·출력한 것이다.
        with suppress(Exception):
            if self.evidence["probe_typed"] and await self.page.evaluate(
                "n => document.body && document.body.innerHTML.includes(n)", self.nonce,
            ):
                mark = {"session": self.session, "url": urlsplit(self.page.url).path,
                        "full_url": masked_url(self.page.url)}
                if mark not in self.evidence["rendered"]:
                    self.evidence["rendered"].append(mark)
        if self.evidence["marker_typed"]:
            with suppress(Exception):
                contexts = await self.page.evaluate(LOCATE_MARKER, self.marker_id)
                known = set(self.contexts())
                if contexts:
                    self.evidence["reflection"].append({"session": self.session, "contexts": contexts,
                                                        "url": urlsplit(self.page.url).path})
                if set(contexts) - known:
                    await self.stage("reflect", "done", ", ".join(self.contexts()))
        if len(self.evidence["executed"]) > executed_before:
            if self.phase == "impact":
                await self.stage("detect", "done", "실행 확인")
            await self.stage(self.phase, "done", "실행 확인")
            await self.capture("executed")
            # 실제 XSS 효과(붉은 화면·흔들림)가 실시간 화면에 남도록 잠시 멈춘다.
            await asyncio.sleep(DIRECT_HOLD if self.direct else EXECUTION_HOLD)

    async def capture(self, label: str):
        self._shots += 1
        name = f"{self.finding.id}-{self.evidence['mode']}-{self._shots:02d}-{label}.png"
        with suppress(Exception):
            await self.page.screenshot(path=str(self.output / name), type="png", timeout=5000)
            self.evidence["screenshots"].append(f"/api/artifacts/{self.run_id}/{name}")

    async def screenshot(self, quality: int = 70) -> str:
        jpeg = await self.page.screenshot(type="jpeg", quality=quality, timeout=5000)
        return base64.b64encode(jpeg).decode()

    async def stage(self, name: str, status: str, detail: str = ""):
        await self.emit({"type": "stage", "stage": name, "status": status, "detail": detail[:160]})

    def contexts(self) -> list[str]:
        seen = []
        for item in self.evidence["reflection"]:
            seen += [c for c in item["contexts"] if c not in seen]
        return seen

    def candidates(self) -> list[str]:
        tried = set(self.evidence["canaries"])
        options = []
        for context in self.contexts():
            options += [c for c in CANARIES_BY_CONTEXT.get(context, []) if c not in options]
        return [c for c in options if c in self.canaries and c not in tried]

    def impact_allowed(self) -> bool:
        # 영향 시연 페이로드는 HTML 본문 출력용이다. 반사 위치가 맞으면 탐지와 시연을 한 번에 한다.
        return self.evidence["impact"] is None and (
            self.direct or bool(self.evidence["executed"]) or "html_unescaped" in self.contexts())

    def csp_settled(self) -> bool:
        # 검사 문자열이 출력됐고 CSP가 실행을 막았다. 다른 문자열도 같은 정책에 막히므로 더 시도할 이유가 없다.
        return self.evidence["csp_blocked"] and bool(self.evidence["rendered"])

    def status(self) -> dict:
        return {
            "marker_reflection": self.contexts(),
            "canary_candidates": self.candidates(),
            "canaries_tried": self.evidence["canaries"],
            "impact_allowed": self.impact_allowed(),
            "url_path": urlsplit(self.page.url).path if self.page else None,
            "session": self.session,
            "probe_typed": self.evidence["probe_typed"],
            "probe_rendered": bool(self.evidence["rendered"]),
            "probe_executed": bool(self.evidence["executed"]),
            "probe_blocked_by_csp": self.csp_settled(),
            "actions_left": MAX_ACTIONS - len(self.evidence["actions"]),
        }

    async def record(self, action: str, reason: str = "", detail: str = ""):
        if len(self.evidence["actions"]) >= MAX_ACTIONS:
            raise ActionError("행동 한도를 초과했습니다. finish로 종료하라.")
        self.evidence["actions"].append({"action": action, "reason": reason[:300],
                                         "detail": detail[:120], "session": self.session})
        await self.emit({"type": "agent", "action": action, "message": reason[:300],
                         "detail": detail[:120]})

    # ---- 조작 ------------------------------------------------------------
    async def click(self, x: int, y: int, button: str = "left", count: int = 1):
        if not (0 <= x < VIEWPORT["width"] and 0 <= y < VIEWPORT["height"]):
            raise ActionError("좌표가 화면 밖이다.")
        # 화면의 커서가 목표 지점까지 미끄러지듯 이동한 뒤 실제 클릭이 일어나도록 맞춘다.
        await self.emit({"type": "cursor", "x": x, "y": y})
        await asyncio.sleep(CURSOR_GLIDE)
        await self.page.mouse.move(x, y, steps=20)
        await self.page.mouse.click(x, y, button=button, click_count=count)
        await self.emit({"type": "click", "x": x, "y": y})
        self.mouse = (x, y)
        await self._settle()
        await self._center_focus()
        await asyncio.sleep(CLICK_DELAY)

    async def _center_focus(self):
        """입력란을 눌렀으면 화면 가운데로 끌어와 입력 과정이 잘 보이게 한다."""
        with suppress(Exception):
            moved = await self.page.evaluate("""() => {
              const e = document.activeElement;
              if (!e || !(e.tagName === 'TEXTAREA' || e.tagName === 'INPUT' || e.isContentEditable)) return false;
              const r = e.getBoundingClientRect();
              if (r.top > innerHeight * 0.2 && r.bottom < innerHeight * 0.8) return false;
              e.scrollIntoView({block: 'center', behavior: 'smooth'});
              return true;
            }""")
            if moved:
                await asyncio.sleep(0.6)

    async def move(self, x: int, y: int):
        await self.emit({"type": "cursor", "x": x, "y": y})
        await asyncio.sleep(CURSOR_GLIDE)
        await self.page.mouse.move(x, y, steps=12)
        self.mouse = (x, y)

    async def type_text(self, text: str):
        if HTML_LIKE.search(text) or len(text) > 200:
            raise ActionError("일반 입력에는 HTML·스크립트를 쓸 수 없다. 취약 후보 필드는 type_probe를 사용하라.")
        await self.page.keyboard.type(text, delay=40)

    async def _require_focus(self):
        focused = await self.page.evaluate(
            "() => { const e = document.activeElement; return !!e && (e.tagName === 'TEXTAREA' || "
            "(e.tagName === 'INPUT' && !['hidden','password','file','checkbox','radio','submit'].includes(e.type)) "
            "|| e.isContentEditable); }")
        if not focused:
            raise ActionError("입력란이 선택되지 않았다. 먼저 입력란을 클릭하라.")

    async def type_marker(self):
        """출력 위치를 알아내기 위한 무해한 반사 마커를 입력한다(실행되는 코드 없음)."""
        await self._require_focus()
        await self.stage("explore", "done", "입력 지점 확인")
        await self.stage("reflect", "running", "필터링 확인 표식 입력")
        await self.page.keyboard.type(self.marker, delay=20)
        self.evidence["marker_typed"] = True
        await asyncio.sleep(0.5)

    async def type_canary(self, canary_id: str):
        if canary_id not in self.canaries:
            raise ActionError("등록되지 않은 카나리다.")
        if len(self.evidence["canaries"]) >= MAX_CANARIES:
            raise ActionError(f"카나리는 최대 {MAX_CANARIES}회까지 시도한다. finish로 종료하라.")
        if self.csp_settled():
            raise ActionError("CSP가 이미 실행을 막았다. 다른 검사 문자열도 같은 정책에 막히므로 not_executed로 finish하라.")
        await self._require_focus()
        self.phase = "detect"
        self.evidence["canaries"].append(canary_id)
        await self.stage("detect", "running", canary_id)
        await self.emit({"type": "probe", "payload_id": canary_id,
                         "message": "출력 위치에 맞춘 검사 문자열을 입력합니다."})
        await self.page.keyboard.type(self.canaries[canary_id], delay=PROBE_TYPE_DELAY_MS * 2)
        self.evidence["probe_typed"] = True
        self.evidence["probe_session"] = self.session
        await self.capture("canary-typed")
        await asyncio.sleep(1.0)

    async def type_impact(self):
        """취약이 확정된 뒤에만 영향 시연용 페이로드를 입력한다(시연용)."""
        if not self.impact_allowed():
            raise ActionError("반사 위치가 HTML 본문으로 확인되거나 취약이 확정된 뒤에만 영향 시연을 할 수 있다.")
        await self._require_focus()
        combined = not self.evidence["executed"]
        self.phase = "impact"
        self.evidence["impact"] = self.payload_id
        if combined:
            # 탐지 겸 영향 시연: 증거 nonce가 들어 있어 이 실행 자체가 탐지 증거가 된다.
            self.evidence["canaries"].append("impact:" + self.payload_id)
            self.evidence["probe_typed"] = True
            self.evidence["probe_session"] = self.session
            await self.stage("detect", "running", "영향 시연 페이로드로 탐지")
        await self.stage("impact", "running", self.payload_id)
        await self.emit({"type": "probe", "payload_id": self.payload_id,
                         "message": "영향 시연용 페이로드를 입력합니다(시연용)."})
        # 입력되는 모습은 보이되 오래 걸리지 않게, 페이로드를 덩어리로 나눠 IMPACT_TYPE_SECONDS 안에 채운다.
        await self.type_fast(self.probe, IMPACT_TYPE_SECONDS)
        await self.capture("impact-typed")
        await asyncio.sleep(1.0)

    async def type_fast(self, text: str, seconds: float):
        steps = max(1, int(seconds / 0.05))
        size = max(1, -(-len(text) // steps))
        for start in range(0, len(text), size):
            await self.page.keyboard.insert_text(text[start:start + size])
            await asyncio.sleep(0.05)

    async def key(self, combo: str, repeat: int = 1):
        for _ in range(max(1, min(repeat, 20))):
            await self.page.keyboard.press(combo)
        await self._settle()

    async def scroll(self, dx: int, dy: int):
        await self.page.mouse.wheel(dx, dy)
        await asyncio.sleep(0.4)

    async def navigate(self, path: str):
        if not path.startswith("/") or path.startswith("//") or not self._allowed(self.target.origin + path):
            raise ActionError("허용된 대상 범위 밖의 경로다.")
        with suppress(Exception):
            await self.page.goto(self.target.origin + path, wait_until="load")
        await self._settle()

    async def new_session(self):
        self.session += 1
        self.evidence["sessions"] = self.session
        await self._open_session()
        await self._settle()

    async def finish(self, conclusion: str, reason: str):
        self.done = {"conclusion": conclusion, "reason": reason[:500]}
        await self.capture("final")

    # ---- 판정 ------------------------------------------------------------
    def verdict(self, error: Exception | None = None) -> dict:
        ev = self.evidence
        executed = ev["executed"]
        fresh = [item for item in executed
                 if ev["probe_session"] is not None and item["session"] > ev["probe_session"]]
        if fresh:
            status, message = "REPRODUCED", "새 세션 재방문에서 저장된 검사 문자열의 실행을 확인했습니다(저장형)."
        elif executed:
            status, message = "REPRODUCED", "같은 세션에서 검사 문자열의 실행을 확인했습니다."
        elif ev["csp_blocked"] and ev["rendered"]:
            status, message = "BLOCKED", "검사 문자열은 출력됐지만 CSP가 실행을 차단했습니다."
        elif ev["rendered"] and self.done and self.done["conclusion"] != "could_not_reach":
            status, message = "NOT_REPRODUCED", "검사 문자열이 저장·출력됐지만 실행되지 않았습니다."
        elif error is not None:
            status = "INCONCLUSIVE"
            message = (str(error) if type(error) in (RuntimeError, ActionError)
                       else "에이전트 검사를 완료하지 못했습니다: " + type(error).__name__)
            ev["error_type"] = type(error).__name__
        else:
            status, message = "INCONCLUSIVE", "검사 문자열의 저장·출력을 확인하지 못해 판정할 수 없습니다."
        ev["agent_conclusion"] = self.done
        ev["reflection_contexts"] = self.contexts()
        return {"finding_id": self.finding.id, "title": self.finding.title, "status": status,
                "message": message, "evidence": ev,
                "duration_ms": round((time.monotonic() - self.started) * 1000)}
