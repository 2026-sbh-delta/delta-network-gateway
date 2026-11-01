"""실행 중인 로컬 DAST 제어 화면을 실제 Chrome으로 검증한다."""

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo-shots"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


async def run_case(page, scenario: str, expected: str, planner: str,
                   report: dict, label: str) -> dict:
    await page.locator("#report-input").evaluate(
        "(element, value) => { element.value = value; }",
        json.dumps(report, ensure_ascii=False),
    )
    await page.locator("#scenario").select_option(scenario)
    await page.locator("#planner").select_option(planner)
    await page.locator("#run").click()
    finding = page.locator('.finding[data-state="%s"]' % expected)
    await finding.wait_for(state="visible", timeout=90_000)
    await page.locator("#run:enabled").wait_for(timeout=10_000)
    await page.screenshot(path=OUT / f"ui-{label}.png", full_page=True)
    report_url = await page.locator("#report-link").get_attribute("href")
    artifact = await page.evaluate(
        "async href => await (await fetch(href)).json()", report_url,
    )
    result = artifact["results"][0]
    return {
        "case": label,
        "scenario": scenario,
        "planner": planner,
        "status": await finding.get_attribute("data-state"),
        "title": await page.locator("#result-title").inner_text(),
        "mapping": await finding.locator(".mapping").inner_text(),
        "browser_started": result.get("evidence", {}).get("browser_started", True),
        "run_duration_ms": artifact.get("duration_ms"),
        "plan_model": artifact.get("plan", {}).get("model_name"),
        "plan_duration_ms": artifact.get("plan", {}).get("duration_ms"),
        "plan_cost_usd": artifact.get("plan", {}).get("cost_usd"),
        "report": report_url,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", default="http://127.0.0.1:8765")
    parser.add_argument("--include-claude", action="store_true")
    args = parser.parse_args()
    sample = json.loads((ROOT / "examples" / "sast-stored-xss.json").read_text())
    aws = json.loads((ROOT / "examples" / "aws-code-review-sample.json").read_text())
    invalid = deepcopy(sample)
    invalid["findings"][0]["trigger"]["route_id"] = "unregistered-route"
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=CHROME,
            headless=True,
            args=["--disable-background-networking", "--disable-component-update"],
        )
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.goto(args.control, wait_until="networkidle")
        await page.locator("#run:enabled").wait_for(timeout=10_000)
        results = []
        for scenario, expected in (
            ("vulnerable", "REPRODUCED"),
            ("fixed", "NOT_REPRODUCED"),
            ("csp", "BLOCKED"),
        ):
            results.append(await run_case(
                page, scenario, expected, "reviewed", sample, f"reviewed-{scenario}",
            ))
        results.append(await run_case(
            page, "vulnerable", "INCONCLUSIVE", "claude", invalid, "invalid-registry",
        ))
        if args.include_claude:
            for scenario, expected in (
                ("vulnerable", "REPRODUCED"),
                ("fixed", "NOT_REPRODUCED"),
            ):
                results.append(await run_case(
                    page, scenario, expected, "claude", aws, f"aws-claude-{scenario}",
                ))
        await browser.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
