// Browser contract against fixture.py using a real operator session and HTTP AI adapter.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const output = process.env.CASE_CONSOLE_EVIDENCE;
if (!output) throw new Error('Set CASE_CONSOLE_EVIDENCE to a dedicated evidence directory');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1080 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [], dialogs = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { dialogs.push(d.message()); d.dismiss(); });
  const step = async (name, action) => { await action(); console.log(name); };
  try {
    await step('Login and list pagination', async () => {
      await page.goto('https://127.0.0.1:8447/');
      await page.locator('#password').fill('fixture-console-password');
      await page.getByRole('button', { name: '로그인', exact: true }).click();
      await page.locator('#open-cases').click();
      await page.locator('.case-row').first().waitFor();
      assert.equal(await page.locator('.case-row').count(), 30);
      await page.locator('#cases-next').click();
      await page.waitForFunction(() => document.querySelector('#cases-page').textContent === '31–32');
      assert.equal(await page.locator('.case-row').count(), 2);
      await page.locator('#cases-prev').click();
      await page.locator('[data-case="case-00"]').waitFor();
    });
    await step('Evidence, XSS escaping and review validation', async () => {
      await page.locator('[data-case="case-00"]').click();
      await page.locator('#case-review-form').waitFor();
      assert.equal(await page.locator('#case-detail img').count(), 0);
      assert(await page.locator('#case-detail').innerText().then(t => t.includes('<img src=x onerror=alert(1)>')));
      assert(await page.locator('#case-export').isDisabled());
      await page.locator('#case-reviewer').fill('console-tester');
      await page.locator('#case-reason').fill('동일 작업 ID와 HTTP 재검사 근거를 확인했습니다.');
      await page.locator('#case-required').fill('HTTP\n재검사');
      await page.locator('#case-forbidden').fill('전체 이전 완료');
      await page.locator('#case-save').click();
      assert.equal(await page.locator('#case-confirm').evaluate(el => el.validity.valueMissing), true);
      await page.locator('#case-confirm').check();
      await page.locator('#case-save').click();
      await page.waitForFunction(() => document.querySelector('#cases-message').textContent === '검토 기록을 저장했습니다.');
      assert(await page.locator('#case-export').isEnabled());
    });
    await step('Pinned evaluation JSON download and unsaved edit guard', async () => {
      const promise = page.waitForEvent('download');
      await page.locator('#case-export').click();
      const download = await promise;
      const file = path.join(output, download.suggestedFilename());
      await download.saveAs(file);
      const dataset = JSON.parse(fs.readFileSync(file));
      assert.equal(dataset.candidates[0].case.request.case_id, 'case-00');
      assert.deepEqual(dataset.candidates[0].review.required_terms, ['HTTP', '재검사']);
      assert.equal(dataset.candidates[0].review.reviewer, 'console-tester');
      await page.locator('#case-reason').fill('저장하지 않은 변경');
      assert(await page.locator('#case-export').isDisabled());
      await page.locator('#case-reason').fill('검토 후 검색 승인을 취소합니다.');
      await page.locator('#case-decision').selectOption('rejected');
      await page.locator('#case-save').click();
      await page.waitForFunction(() => document.querySelector('#cases-message').textContent === '검토 기록을 저장했습니다.');
      assert(await page.locator('#case-export').isDisabled());
      await page.locator('.case-audit summary').click();
      assert.equal(await page.locator('.case-audit article').count(), 2);
    });
    await step('Unverified outcome cannot be approved', async () => {
      await page.locator('[data-case="case-01"]').click();
      await page.waitForFunction(() => document.querySelector('.case-facts')?.textContent.includes('case-01'));
      assert.equal(await page.locator('#case-decision option[value=approved]').evaluate(el => el.disabled), true);
      assert.equal(await page.locator('#case-decision').inputValue(), 'rejected');
    });
    await step('Concurrent new version is refreshed after conflict', async () => {
      await page.locator('[data-case="case-03"]').click();
      await page.waitForFunction(() => document.querySelector('.case-facts')?.textContent.includes('case-03'));
      execFileSync(process.env.CASE_CONSOLE_PYTHON, ['-c', `
from pathlib import Path
from delta_ai.cases import CaseStore,IncidentCase
store=CaseStore(Path('/private/tmp/delta-case-console-fixture/data/cases.sqlite'))
old=IncidentCase.model_validate_json(store.detail('case-03')['payload'])
store.publish(old.model_copy(update={'sequence':1}))
`], { env: process.env });
      await page.locator('#case-reviewer').fill('console-tester');
      await page.locator('#case-reason').fill('오래된 화면에서 제출한 검토');
      await page.locator('#case-confirm').check();
      await page.locator('#case-save').click();
      await page.waitForFunction(() => document.querySelector('#cases-message').textContent.includes('최신 근거를 다시 확인'));
      assert(await page.locator('.case-facts').innerText().then(t => t.includes('순서 1')));
      assert.equal(await page.locator('#case-reason').inputValue(), '');
      await page.locator('#case-reviewer').fill('console-tester');
      await page.locator('#case-reason').fill('새 버전의 재검사 근거를 확인했습니다.');
      await page.locator('#case-required').fill('HTTP');
      await page.locator('#case-confirm').check();
      await page.locator('#case-save').click();
      await page.waitForFunction(() => document.querySelector('#cases-message').textContent === '검토 기록을 저장했습니다.');
      assert(await page.locator('.case-columns').evaluate(el => el.scrollHeight <= el.clientHeight + 2));
      await page.locator('#cases').evaluate(el => { el.scrollTop = 0; });
      await page.locator('#case-detail').evaluate(el => { el.scrollTop = 0; });
      await page.locator('#cases').screenshot({ path: path.join(output, 'desktop.png') });
      await page.locator('#case-save').scrollIntoViewIfNeeded();
      await page.locator('#cases').screenshot({ path: path.join(output, 'desktop-review.png') });
    });
    await step('Mobile layout and loading error recovery', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator('#cases').screenshot({ path: path.join(output, 'mobile.png') });
      assert(await page.locator('#cases').evaluate(el => el.scrollWidth <= el.clientWidth));
      await page.route('**/api/case-reviews?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"사례 검토 전용 연결이 설정되지 않았습니다"}' }));
      await page.locator('#cases-refresh').click();
      await page.waitForFunction(() => document.querySelector('#cases-message').textContent.includes('설정되지 않았습니다'));
      await page.unroute('**/api/case-reviews?*');
      await page.locator('#cases-refresh').click();
      await page.locator('.case-row').first().waitFor();
      await page.locator('#close-cases').click();
      assert.equal(await page.locator('#cases').evaluate(el => el.open), false);
    });
    assert.deepEqual(errors, []);
    assert.deepEqual(dialogs, []);
    fs.writeFileSync(path.join(output, 'browser-summary.json'), JSON.stringify({ passed: 6, pageErrors: errors, unexpectedDialogs: dialogs, actualModelCalls: 0, cloudCalls: 0 }, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
