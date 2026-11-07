const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../delta_orchestrator/static/mapping.js"), "utf8"), context);
const {render, readRequest} = context.window.DeltaMapping;
const detail = (changes = {}) => ({source_provider:"aws", target_provider:"gcp", verdict:"needs_human", mapping:{rows:[], assumptions:["테스트 가정"], cost:{source_region:"ap-northeast-2", target_region:"asia-northeast3", currency:"USD", hours_per_month:730, priced_on:"2026-11-02", lines:[{server:"web<script>alert(1)</script>", source_type:"t4g.nano", target_type:"e2-micro", source_monthly:3.8, target_monthly:7.84, difference:4.04}], source_total:3.8, target_total:7.84, difference_total:4.04, excludes:["디스크", "네트워크"], ...changes}}});

test("saved server prices, totals and basis are visible and names are escaped", () => {
  const html = render(detail());
  for (const text of ["$3.80", "$7.84", "$4.04 증가", "730시간", "2026-11-02", "사람 확인 필요", "디스크", "테스트 가정"]) assert.ok(html.includes(text), text);
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(!html.includes("<script>"));
});
test("unknown prices remain unknown, and failure does not show a stale table", () => {
  assert.ok(render(detail({source_total:null, target_total:null, difference_total:null})).includes("산정 불가"));
  assert.ok(!render({...detail(), error:"연결 실패"}).includes("<table"));
  assert.ok(render({...detail(), error:"연결 실패"}).includes('role="alert"'));
  assert.ok(!render({}).includes("$0.00"));
  assert.ok(render({mapping:{rows:[], cost:null}}).includes("비교 가능한 서버 비용이 없습니다"));
});
test("decreases and real zero values are distinct from missing costs", () => {
  assert.ok(render(detail({difference_total:-1.25})).includes("$1.25 감소"));
  assert.ok(render(detail({source_total:0, difference_total:0})).includes("$0.00 · 동일"));
});
test("upload accepts normalized extraction and rejects raw state, size and malformed JSON", async () => {
  assert.equal(await readRequest(null), null);
  await assert.rejects(readRequest({size:60001}), /60KB/);
  await assert.rejects(readRequest({size:5, text:async()=>"wrong"}), /JSON/);
  await assert.rejects(readRequest({size:2, text:async()=>'{"values":{}}'}), /원본 state/);
  const value = {kind:"migration_mapping", evidence:{mapping:{servers:[]}}};
  assert.equal((await readRequest({size:50,text:async()=>JSON.stringify(value)})).kind, "migration_mapping");
});
