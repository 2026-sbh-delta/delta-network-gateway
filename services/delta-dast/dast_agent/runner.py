"""등록된 폼 저장 후 새 브라우저 컨텍스트에서만 실행 증거를 수집한다."""

import asyncio
import base64
from contextlib import suppress
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from .contracts import Finding
from .registry import ResolvedRoute


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def browser_executable(playwright=None) -> str | None:
    configured = os.environ.get("DAST_BROWSER_EXECUTABLE")
    if configured:
        return configured if Path(configured).is_file() and os.access(configured, os.X_OK) else None
    for candidate in (CHROME, "/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome",
                      playwright.chromium.executable_path if playwright else ""):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


async def browser_available() -> bool:
    async with async_playwright() as playwright:
        return browser_executable(playwright) is not None


async def verify(finding: Finding, route: ResolvedRoute, payload_id: str,
                 payload_template: str, run_id: str, output: Path, emit) -> dict:
    nonce = secrets.token_hex(16)
    origin = route.origin
    payload = payload_template.replace("{{DAST_NONCE}}", nonce)
    evidence = {
        "nonce": nonce,
        "console": [],
        "screenshots": [],
        "control_passed": False,
        "fresh_context": False,
        "proof_marker": False,
        "console_proof": False,
        "target_id": route.target_id,
        "route_id": route.route_id,
        "field": route.field,
        "payload_id": payload_id,
    }
    started = time.monotonic()
    page = None
    frame_task = None
    browser = None

    async def state(phase, message):
        await emit({"type": "state", "phase": phase, "message": message})

    async def frames():
        while True:
            tick = time.monotonic()
            current = page
            if current is not None and not current.is_closed():
                try:
                    jpeg = await current.screenshot(type="jpeg", quality=50, timeout=1800)
                    await emit({"type": "frame", "image": "data:image/jpeg;base64," +
                                base64.b64encode(jpeg).decode(), "width": 960, "height": 640})
                except Exception:
                    pass
            await asyncio.sleep(max(0.01, 0.5 - (time.monotonic() - tick)))

    async def move_click(locator):
        await locator.wait_for(state="visible")
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        if not box:
            raise RuntimeError("클릭할 요소가 보이지 않습니다.")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await page.mouse.move(x, y, steps=12)
        await emit({"type": "cursor", "x": x, "y": y})
        await page.mouse.click(x, y)
        await emit({"type": "cursor", "x": x, "y": y})

    async def capture(label):
        name = f"{finding.id}-{label}.png"
        await page.screenshot(path=str(output / name), type="png", timeout=5000)
        evidence["screenshots"].append(f"/api/artifacts/{run_id}/{name}")

    async def new_page():
        nonlocal page
        context = await browser.new_context(
            viewport={"width": 960, "height": 640},
            service_workers="block",
            accept_downloads=False,
        )

        async def guard(playwright_route):
            request = playwright_route.request
            parsed = urlsplit(request.url)
            same_origin = f"{parsed.scheme}://{parsed.netloc}" == origin
            if (same_origin and not parsed.query and not parsed.fragment
                    and route.permits(request.method, parsed.path)):
                from .cloud_run import authorization

                auth = await asyncio.to_thread(authorization, origin)
                response = await playwright_route.fetch(max_redirects=0, timeout=10000,
                                                        headers={**request.headers, **auth})
                if 300 <= response.status < 400:
                    await playwright_route.abort("blockedbyclient")
                else:
                    await playwright_route.fulfill(response=response)
            else:
                await playwright_route.abort("blockedbyclient")

        await context.route("**/*", guard)
        await context.route_web_socket("**/*", lambda socket: socket.close())
        page = await context.new_page()
        page.set_default_timeout(7000)
        page.set_default_navigation_timeout(10000)
        context.on("page", lambda extra: asyncio.create_task(extra.close()))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        return page

    async def navigate_board():
        expected = origin + route.board_path
        response = await page.goto(expected, wait_until="load")
        if not response or response.status != 200 or page.url != expected:
            raise RuntimeError("등록된 게시판을 정상적으로 열 수 없습니다.")

    async def store(body: str, title: str) -> str:
        await navigate_board()
        await move_click(page.locator(route.create_link_selector))
        await page.wait_for_url(origin + route.create_path)
        if route.title_selector:
            await move_click(page.locator(route.title_selector))
            await page.locator(route.title_selector).press_sequentially(title, delay=12)
        await move_click(page.locator(route.input_selector))
        await page.locator(route.input_selector).press_sequentially(body, delay=1)
        expected_submit = origin + route.submit_path
        async with page.expect_response(
            lambda response: response.url == expected_submit and response.request.method == "POST"
        ) as pending:
            await move_click(page.locator(route.submit_selector))
        response = await pending.value
        if response.status != 200:
            raise RuntimeError("등록된 폼 저장에 실패했습니다.")
        saved = page.locator(route.saved_detail_link_selector)
        await saved.wait_for()
        relative = await saved.get_attribute("href")
        if not relative or not route.detail_path_regex.fullmatch(relative):
            raise RuntimeError("저장된 상세 주소가 registry 경로와 일치하지 않습니다.")
        if await page.evaluate("typeof globalThis.__dastProof !== 'undefined'"):
            raise RuntimeError("저장 확인 화면에서 실행 흔적이 발견되었습니다.")
        return relative

    async def reopen(relative: str):
        await page.context.close()
        await new_page()
        await navigate_board()
        async with page.expect_response(
            lambda response: response.url == origin + relative and response.request.method == "GET"
        ) as pending:
            await move_click(page.locator(f'a[href="{relative}"]'))
        response = await pending.value
        await page.wait_for_url(origin + relative, wait_until="load")
        if response.status != 200 or page.url != origin + relative:
            raise RuntimeError("저장된 상세 페이지 재방문에 실패했습니다.")
        return response

    try:
        async with async_playwright() as playwright:
            executable = browser_executable(playwright)
            if not executable:
                raise RuntimeError("사용 가능한 Chrome 또는 Chromium 실행 파일이 없습니다.")
            browser = await playwright.chromium.launch(
                executable_path=executable,
                headless=True,
                args=["--disable-background-networking", "--disable-component-update"],
            )
            try:
                async with asyncio.timeout(65):
                    await new_page()
                    frame_task = asyncio.create_task(frames())
                    await state("control", "정상 입력을 등록된 폼으로 저장하고 새 브라우저에서 확인합니다.")
                    control_text = "정상 입력 확인 " + nonce
                    control_path = await store(control_text, "정상 동작 확인 " + nonce[:8])
                    await reopen(control_path)
                    if await page.locator(route.output_selector).inner_text() != control_text:
                        raise RuntimeError("정상 입력의 저장 및 표시 확인에 실패했습니다.")
                    evidence.update(control_passed=True, control_post_path=control_path)
                    await capture("control")
                    await state("input", "등록된 payload를 SAST 매핑의 입력 필드에 저장합니다.")
                    attack_path = await store(payload, route.title_value + " " + nonce[:8])
                    evidence["post_path"] = attack_path
                    evidence["save_response_has_no_execution"] = True
                    await capture("saved")
                    await state("revisit", "새 컨텍스트에서 등록된 목록·상세 경로를 재방문합니다.")
                    await page.context.close()
                    await new_page()
                    evidence["fresh_context"] = True
                    observed = []
                    page_errors = []

                    def console(message):
                        value = message.text
                        if (value == "DAST_PROOF:" + nonce or "content security policy" in value.lower()
                                or "violates the following" in value.lower()):
                            observed.append({"type": message.type, "text": value[:2400]})

                    page.on("console", console)
                    page.on("pageerror", lambda error: page_errors.append(type(error).__name__))
                    await navigate_board()
                    async with page.expect_response(
                        lambda response: response.url == origin + attack_path
                        and response.request.method == "GET"
                    ) as pending:
                        await move_click(page.locator(f'a[href="{attack_path}"]'))
                    response = await pending.value
                    await page.wait_for_url(origin + attack_path, wait_until="load")
                    if response.status != 200 or page.url != origin + attack_path:
                        raise RuntimeError("공격 게시글의 상세 재방문에 실패했습니다.")
                    evidence["detail_url"] = page.url
                    evidence["csp_header"] = response.headers.get("content-security-policy", "")
                    await state("observe", "nonce가 일치하는 실행 표식과 브라우저 콘솔을 관측합니다.")
                    await asyncio.sleep(0.2)
                    await capture("observation")
                    await asyncio.sleep(3.2)
                    marker = await page.evaluate("nonce => globalThis.__dastProof === nonce", nonce)
                    console_proof = any(item["text"] == "DAST_PROOF:" + nonce for item in observed)
                    output_node = page.locator(route.output_selector)
                    visible_text = await output_node.inner_text()
                    markup = await output_node.inner_html()
                    stored_payload = visible_text == payload or nonce in markup
                    evidence.update(
                        proof_marker=marker,
                        console_proof=console_proof,
                        console=observed,
                        page_errors=page_errors,
                        stored_payload_present=stored_payload,
                        visual_restored=await page.locator("#dast-visual-proof").count() == 0,
                    )
                    for entry in observed:
                        await emit({"type": "log", "message": entry["text"]})
                    await capture("after")
                    csp_block = (
                        stored_payload
                        and "script-src 'none'" in evidence["csp_header"]
                        and any(
                            "inline" in item["text"].lower()
                            and ("violat" in item["text"].lower() or "refused" in item["text"].lower())
                            and "script-src" in item["text"].lower()
                            for item in observed
                        )
                    )
                    if marker and console_proof:
                        status, message = "REPRODUCED", "새 컨텍스트에서 저장된 payload의 JS 실행을 확인했습니다."
                    elif marker or console_proof:
                        status, message = "INCONCLUSIVE", "실행 표식과 콘솔 증거가 일치하지 않습니다."
                    elif csp_block:
                        status, message = "BLOCKED", "저장된 인라인 이벤트를 CSP가 차단한 직접 증거를 확인했습니다."
                    elif page_errors:
                        status, message = "INCONCLUSIVE", "페이지의 스크립트 오류로 실행 여부를 판정할 수 없습니다."
                    elif stored_payload:
                        status, message = "NOT_REPRODUCED", "이 payload와 재방문 조건에서는 실행이 재현되지 않았습니다."
                    else:
                        status, message = "INCONCLUSIVE", "저장된 payload를 상세 페이지에서 확인할 수 없습니다."
            finally:
                if frame_task:
                    frame_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await frame_task
                await browser.close()
    except Exception as exc:
        status = "INCONCLUSIVE"
        message = (str(exc) if type(exc) is RuntimeError else
                   "브라우저 검증을 완료하지 못했습니다: " + type(exc).__name__)
        evidence["error_type"] = type(exc).__name__
    return {
        "finding_id": finding.id,
        "title": finding.title,
        "status": status,
        "message": message,
        "evidence": evidence,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
