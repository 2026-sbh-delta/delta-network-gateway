// Real browser keeps the same cookie across a full app/database cutover.
const fs = require('node:fs');
const path = require('node:path');
const { chromium, expect } = require('../web/node_modules/@playwright/test');
const root = path.resolve(__dirname, '../.runtime/migration-demo');
const settings = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8').trim().split('\n').map(line => {
  const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)];
}));
const signal = async name => {
  const deadline = Date.now() + 300000;
  while (!fs.existsSync(path.join(root, name))) {
    if (Date.now() > deadline) throw Error('Timed out waiting for ' + name);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:18096/');
    await page.locator('[name=username]').fill('migration-demo');
    await page.locator('[name=password]').fill(settings.DEMO_PASSWORD);
    await page.getByRole('button', { name: '로그인' }).click();
    await expect(page.locator('.badge')).toContainText('local-source');
    await page.locator('[name=text]').fill('이전 전 작성한 메모');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByText('이전 전 작성한 메모', { exact: false })).toBeVisible();
    const cookieBefore = (await page.context().cookies()).find(c => c.name === 'delta_demo_session').value;
    await page.screenshot({ path: path.join(root, 'before.png'), fullPage: true });
    fs.writeFileSync(path.join(root, 'browser-ready'), 'ready');
    await signal('copy-ready');
    await page.locator('[name=text]').fill('동기화 중 작성한 메모');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByText('동기화 중 작성한 메모', { exact: false })).toBeVisible();
    fs.writeFileSync(path.join(root, 'browser-cdc-ready'), 'ready');
    await signal('cutover-done');
    await page.reload();
    await expect(page.locator('.badge')).toContainText('local-target');
    await expect(page.getByText('이전 전 작성한 메모', { exact: false })).toBeVisible();
    await expect(page.getByText('동기화 중 작성한 메모', { exact: false })).toBeVisible();
    const cookieAfter = (await page.context().cookies()).find(c => c.name === 'delta_demo_session').value;
    expect(cookieAfter).toBe(cookieBefore);
    await page.locator('[name=text]').fill('이전 후 새로 저장한 메모');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByText('이전 후 새로 저장한 메모', { exact: false })).toBeVisible();
    expect(page.url()).toBe('http://127.0.0.1:18096/');
    expect(errors).toEqual([]);
    await page.screenshot({ path: path.join(root, 'after.png'), fullPage: true });
    fs.writeFileSync(path.join(root, 'browser-proof.json'), JSON.stringify({
      same_url: true, same_session_cookie: true, login_count: 1,
      before_and_cdc_notes_present: true, new_note_saved: true, page_errors: errors,
      scope: 'actual local browser and two PostgreSQL databases; not cloud migration',
    }, null, 2), { mode: 0o600 });
    console.log('Same URL/session; initial/CDC notes preserved; target write passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
