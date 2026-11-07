import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import ts from "typescript";

export const webRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const baselineRoot = path.join(webRoot, "production-ui");
export const sha = (data) => createHash("sha256").update(data).digest("hex");

// The published UI has additional modules whose original TSX is unavailable.
// Keep a verified immutable baseline instead of reconstructing those behaviors.
export function prepareBaseline() {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(baselineRoot, "manifest.json")),
  );
  const archive = path.join(baselineRoot, "baseline.tar.gz");
  if (sha(fs.readFileSync(archive)) !== manifest.archive_sha256)
    throw new Error("Production baseline archive changed");
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "delta-production-ui-"),
  );
  execFileSync("tar", ["-xzf", archive, "-C", directory]);
  for (const [name, digest] of Object.entries(manifest.files)) {
    if (path.isAbsolute(name) || name.split("/").includes(".."))
      throw new Error("Invalid baseline asset path");
    if (sha(fs.readFileSync(path.join(directory, name))) !== digest)
      throw new Error("Production baseline asset changed: " + name);
  }
  const html = fs.readFileSync(path.join(directory, "index.html"), "utf8");
  const primary = html.match(
    /<script type="module" crossorigin src="\/(assets\/index-[^"]+\.js)"><\/script>/,
  )?.[1];
  if (!primary || !manifest.files[primary])
    throw new Error("Missing primary baseline module");
  const source = fs.readFileSync(path.join(directory, primary), "utf8");
  const ast = ts.createSourceFile(
    primary,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JS,
  );
  if (ast.parseDiagnostics.length)
    throw new Error("Baseline JavaScript parse failed");
  const edits = [];
  const counts = { routes: 0, menus: 0, icons: 0, screens: 0 };
  const menu =
    '{view:"pipeline",title:"파이프라인",description:"검사와 운영 반영의 실행 기록"}';
  function visit(node) {
    if (
      node.kind === ts.SyntaxKind.RegularExpressionLiteral &&
      node.getText(ast).includes("overview|build|migration|monitoring")
    ) {
      edits.push({
        start: node.getStart(ast),
        end: node.end,
        value: node
          .getText(ast)
          .replace(
            "overview|build|migration|monitoring",
            "overview|pipeline|build|migration|monitoring",
          ),
      });
      counts.routes++;
    }
    if (ts.isArrayLiteralExpression(node)) {
      const views = node.elements.map((element) => {
        if (!ts.isObjectLiteralExpression(element)) return null;
        const field = element.properties.find(
          (prop) =>
            ts.isPropertyAssignment(prop) && prop.name.getText(ast) === "view",
        );
        return field && ts.isStringLiteral(field.initializer)
          ? field.initializer.text
          : null;
      });
      if (
        views[0] === "overview" &&
        views.includes("migration") &&
        views.includes("monitoring")
      ) {
        edits.push({
          start: node.end - 1,
          end: node.end - 1,
          value: "," + menu,
        });
        counts.menus++;
      }
    }
    if (
      ts.isObjectLiteralExpression(node) &&
      node.properties.some(
        (prop) =>
          ts.isPropertyAssignment(prop) &&
          prop.name.getText(ast) === "overview" &&
          ts.isStringLiteral(prop.initializer) &&
          prop.initializer.text.startsWith("M3 3h7"),
      )
    ) {
      edits.push({
        start: node.end - 1,
        end: node.end - 1,
        value: ',pipeline:"M4 6h6v6H4z M14 12h6v6h-6z M10 9h7v3"',
      });
      counts.icons++;
    }
    if (
      ts.isConditionalExpression(node) &&
      /^(\w+)\.view==="monitoring"&&\1\.serviceId$/.test(
        node.condition.getText(ast),
      )
    ) {
      const route = node.condition.getText(ast).match(/^(\w+)\.view/)[1];
      if (!ts.isCallExpression(node.whenTrue))
        throw new Error("Unexpected existing screen expression");
      const jsx = node.whenTrue.expression.getText(ast);
      edits.push({
        start: node.getStart(ast),
        end: node.getStart(ast),
        value: `${route}.view==="pipeline"?${jsx}("div",{id:"delta-pipeline-root",className:"production-pipeline"}):`,
      });
      counts.screens++;
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  if (
    JSON.stringify(counts) !==
    JSON.stringify({ routes: 1, menus: 2, icons: 1, screens: 1 })
  )
    throw new Error("Unexpected baseline structure: " + JSON.stringify(counts));
  edits.sort((a, b) => a.start - b.start);
  for (let i = 1; i < edits.length; i++)
    if (edits[i].start < edits[i - 1].end)
      throw new Error("Overlapping baseline edits");
  let patched = source;
  for (const edit of [...edits].reverse())
    patched =
      patched.slice(0, edit.start) + edit.value + patched.slice(edit.end);
  if (
    ts.createSourceFile(
      "patched.js",
      patched,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.JS,
    ).parseDiagnostics.length
  )
    throw new Error("Integrated JavaScript parse failed");
  // Reversibility proves every original byte outside these bounded edits is preserved.
  let restored = patched;
  let offset = 0;
  const locations = edits.map((edit) => {
    const position = edit.start + offset;
    offset += edit.value.length - (edit.end - edit.start);
    return { ...edit, position };
  });
  for (const edit of locations.reverse())
    restored =
      restored.slice(0, edit.position) +
      source.slice(edit.start, edit.end) +
      restored.slice(edit.position + edit.value.length);
  if (restored !== source)
    throw new Error("Unrelated production JavaScript changed");
  const entry = `assets/index-production-pipeline-${sha(patched).slice(0, 16)}.js`;
  fs.writeFileSync(path.join(directory, entry), patched);
  return {
    directory,
    html: html.replace(`/${primary}`, `/${entry}`),
    manifest,
    entry,
    counts,
    edits,
  };
}

export function buildProductionUI() {
  const baseline = prepareBaseline();
  const output = path.join(webRoot, "dist");
  const extension = path.join(webRoot, "../.runtime/pipeline-extension");
  try {
    fs.rmSync(output, { recursive: true, force: true });
    fs.cpSync(baseline.directory, output, { recursive: true });
    const entries = fs.readdirSync(extension);
    const js = entries.filter((name) => name.endsWith(".js"));
    const css = entries.filter((name) => name.endsWith(".css"));
    if (js.length !== 1 || css.length !== 1 || entries.length !== 2)
      throw new Error("Expected one self-contained extension and stylesheet");
    const additions = [];
    for (const name of entries) {
      const data = fs.readFileSync(path.join(extension, name));
      const target = `assets/pipeline-extension-${sha(data).slice(0, 16)}${path.extname(name)}`;
      fs.writeFileSync(path.join(output, target), data);
      additions.push(target);
    }
    const script = additions.find((name) => name.endsWith(".js"));
    const style = additions.find((name) => name.endsWith(".css"));
    const html = baseline.html
      .replace("</head>", `<link rel="stylesheet" href="/${style}">\n</head>`)
      .replace(
        "</body>",
        `<script type="module" src="/${script}"></script>\n</body>`,
      );
    fs.writeFileSync(path.join(output, "index.html"), html);
    const files = Object.fromEntries(
      ["index.html", baseline.entry, ...additions].map((name) => [
        name,
        sha(fs.readFileSync(path.join(output, name))),
      ]),
    );
    fs.writeFileSync(
      path.join(output, "production-ui-manifest.json"),
      JSON.stringify(
        {
          baseline: baseline.manifest.files,
          files,
          edits: baseline.counts,
          baseline_release: baseline.manifest.release,
          original_js_preserved_outside_edits: true,
        },
        null,
        2,
      ) + "\n",
    );
    console.log(
      "Production UI built; original assets preserved, pipeline route and extension added.",
    );
  } finally {
    fs.rmSync(baseline.directory, { recursive: true, force: true });
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url) &&
  process.argv[2] === "build"
)
  buildProductionUI();
