import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import ts from "../../web/node_modules/typescript/lib/typescript.js";

const [inputArg, outputArg] = process.argv.slice(2);
if (!inputArg || !outputArg)
  throw new Error(
    "Usage: node hide-ui-notices.mjs INPUT_DIRECTORY OUTPUT_DIRECTORY",
  );
const input = path.resolve(inputArg);
const output = path.resolve(outputArg);
if (fs.existsSync(output)) throw new Error("Output must be a new directory");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const baseline = JSON.parse(
  fs.readFileSync(path.join(input, "assets-manifest.json"), "utf8"),
);
for (const [file, digest] of Object.entries(baseline)) {
  if (path.isAbsolute(file) || file.split("/").includes(".."))
    throw new Error("Unexpected asset path");
  if (hash(fs.readFileSync(path.join(input, file))) !== digest)
    throw new Error("Baseline changed: " + file);
}
const html = fs.readFileSync(path.join(input, "index.html"), "utf8");
const primary = html.match(
  /<script type="module" crossorigin src="\/(assets\/index-[^"]+\.js)"><\/script>/,
)?.[1];
if (!primary || !Object.hasOwn(baseline, primary))
  throw new Error("Expected the existing primary application module");
const source = fs.readFileSync(path.join(input, primary), "utf8");
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
function visit(node) {
  if (
    ts.isCallExpression(node) &&
    node.arguments.length > 0 &&
    ts.isStringLiteral(node.arguments[0]) &&
    node.arguments[0].text === "p" &&
    node.getText(ast).includes("배포 주소 확인") &&
    node.getText(ast).includes("사설 주소:")
  ) {
    let owner = node.parent;
    while (owner && !ts.isFunctionDeclaration(owner)) owner = owner.parent;
    if (!owner?.getText(ast).includes("/migration-pipeline"))
      throw new Error("Unexpected address paragraph owner");
    edits.push({ start: node.getStart(ast), end: node.end });
    return;
  }
  ts.forEachChild(node, visit);
}
visit(ast);
if (edits.length !== 1)
  throw new Error("Expected one migration address template");
const edit = edits[0];
const patched = source.slice(0, edit.start) + "null" + source.slice(edit.end);
if (
  ts.createSourceFile(
    "patched.js",
    patched,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JS,
  ).parseDiagnostics.length
)
  throw new Error("Patched JavaScript parse failed");
if (
  patched.slice(0, edit.start) +
    source.slice(edit.start, edit.end) +
    patched.slice(edit.start + 4) !==
  source
)
  throw new Error("Unrelated JavaScript changed");
const css = fs.readFileSync(
  new URL("../../web/src/ui-visibility.css", import.meta.url),
);
const jsPath = `assets/index-ui-hidden-${hash(patched).slice(0, 16)}.js`;
const cssPath = `assets/ui-visibility-${hash(css).slice(0, 16)}.css`;
fs.mkdirSync(output, { recursive: true });
for (const file of Object.keys(baseline)) {
  const target = path.join(output, file);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(path.join(input, file), target);
}
fs.writeFileSync(path.join(output, jsPath), patched);
fs.writeFileSync(path.join(output, cssPath), css);
fs.writeFileSync(
  path.join(output, "index.html"),
  html
    .replace(`/${primary}`, `/${jsPath}`)
    .replace("</head>", `<link rel="stylesheet" href="/${cssPath}">\n</head>`),
);
const manifest = {
  baseline,
  files: Object.fromEntries(
    ["index.html", jsPath, cssPath].map((file) => [
      file,
      hash(fs.readFileSync(path.join(output, file))),
    ]),
  ),
  edits,
  original_js_preserved_outside_edits: true,
};
fs.writeFileSync(
  path.join(output, "visibility-manifest.json"),
  JSON.stringify(manifest, null, 2) + "\n",
);
console.log(
  JSON.stringify({
    files: manifest.files,
    removed_address_templates: edits.length,
  }),
);
