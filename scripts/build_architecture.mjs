#!/usr/bin/env node
/** Build a static atlas from reviewed content + real ASTs, without starting SongCut. */
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const home = path.join(root, "docs/architecture");
const require = createRequire(import.meta.url);
const ts = require("../apps/web/node_modules/typescript");
const esbuild = require("../apps/web/node_modules/esbuild");
const git = (args) =>
  execFileSync("git", args, {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  }).trim();
const revision = git(["rev-parse", "HEAD"]);
const dirty = git([
  "diff",
  "--name-only",
  "HEAD",
  "--",
  "apps/web/src",
  "apps/desktop/src-tauri/src",
  "services/studio/studio",
]);
if (dirty)
  throw Error(
    "Commit business source changes before rebuilding precise source links.",
  );
const data = JSON.parse(
  fs.readFileSync(path.join(home, "content.json"), "utf8"),
);
const referenced = new Set();
function collect(v) {
  if (Array.isArray(v)) return v.forEach(collect);
  if (v && typeof v === "object") {
    if (typeof v.path === "string" && typeof v.line === "number" && !v.method)
      referenced.add(v.path);
    Object.values(v).forEach(collect);
  }
}
collect(data);
const paths = git(["ls-files"])
  .split("\n")
  .filter(
    (p) =>
      (/\.(?:py|ts|tsx|rs|mjs|json|yaml|yml|sh|toml|md|css|cmake)$/.test(p) ||
        referenced.has(p) ||
        [
          "Dockerfile",
          "LICENSE",
          ".env.example",
          ".env.server.example",
          ".gitignore",
        ].includes(p)) &&
      !p.includes("package-lock") &&
      !p.startsWith("docs/architecture/") &&
      !p.includes("build_architecture"),
  );
const texts = Object.fromEntries(
  paths.map((p) => [
    p,
    execFileSync("git", ["show", `${revision}:${p}`], {
      cwd: root,
      encoding: "utf8",
      maxBuffer: 5 * 1024 * 1024,
    }),
  ]),
);
const python = JSON.parse(
  execFileSync("python3", [path.join(home, "snapshot.py")], {
    input: JSON.stringify(texts),
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  }),
);
const lang = (p) =>
  p.endsWith(".tsx")
    ? "TSX"
    : p.endsWith(".ts")
      ? "TypeScript"
      : p.endsWith(".py")
        ? "Python"
        : p.endsWith(".rs")
          ? "Rust"
          : p.endsWith(".mjs")
            ? "JavaScript"
            : p.endsWith(".css")
              ? "CSS"
              : p.endsWith(".md")
                ? "Markdown"
                : "配置 / 脚本";
const category = (p) =>
  p.includes("/tests/")
    ? "test"
    : p.endsWith(".d.ts")
      ? "declaration"
      : p.startsWith("apps/web/src/") ||
          p.startsWith("services/studio/studio/") ||
          p.startsWith("apps/desktop/src-tauri/src/")
        ? "source"
        : p.startsWith("scripts/")
          ? "script"
          : p.endsWith(".md")
            ? "documentation"
            : "configuration";
function tsSymbols(p, source) {
  const sf = ts.createSourceFile(
      p,
      source,
      ts.ScriptTarget.Latest,
      true,
      p.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    ),
    symbols = [],
    imports = [];
  const lines = source.split(/\r?\n/);
  const add = (node, name, kind) => {
    const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
    const end = sf.getLineAndCharacterOfPosition(node.end).line + 1;
    symbols.push({
      name,
      kind,
      line,
      snippet: lines.slice(line - 1, Math.min(end, line + 11)).join("\n"),
    });
  };
  const visit = (node, scope = "") => {
    if (
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier)
    )
      imports.push(node.moduleSpecifier.text);
    if (ts.isFunctionDeclaration(node) && node.name) {
      const name = scope ? `${scope}.${node.name.text}` : node.name.text;
      add(node, name, "function");
      ts.forEachChild(node, (n) => visit(n, name));
      return;
    }
    if (ts.isClassDeclaration(node) && node.name) {
      add(node, node.name.text, "class");
      ts.forEachChild(node, (n) => visit(n, node.name.text));
      return;
    }
    if (ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)) {
      const name = ts.isConstructorDeclaration(node)
        ? "constructor"
        : node.name.getText(sf);
      add(node, scope ? `${scope}.${name}` : name, "method");
    }
    if (
      (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) &&
      node.name
    )
      add(node, node.name.text, "type");
    if (ts.isVariableDeclaration(node) && node.initializer) {
      if (
        ts.isArrowFunction(node.initializer) ||
        ts.isFunctionExpression(node.initializer)
      )
        add(
          node,
          scope ? `${scope}.${node.name.getText(sf)}` : node.name.getText(sf),
          "function",
        );
      if (ts.isObjectLiteralExpression(node.initializer))
        for (const prop of node.initializer.properties)
          if (
            ts.isPropertyAssignment(prop) &&
            (ts.isArrowFunction(prop.initializer) ||
              ts.isFunctionExpression(prop.initializer))
          )
            add(
              prop,
              `${node.name.getText(sf)}.${prop.name.getText(sf)}`,
              "method",
            );
    }
    ts.forEachChild(node, (n) => visit(n, scope));
  };
  visit(sf);
  return { symbols, imports: [...new Set(imports)] };
}
const files = paths.map((p) => {
  const source = texts[p],
    lines = source.split(/\r?\n/);
  if (source.endsWith("\n")) lines.pop();
  let extracted =
    python[p] ||
    (/\.(ts|tsx|mjs)$/.test(p)
      ? tsSymbols(p, source)
      : { symbols: [], imports: [] });
  if (p.endsWith(".rs"))
    extracted = {
      symbols: [
        ...source.matchAll(
          /^\s*(?:(?:pub|async)\s+)*(fn|struct|enum|impl)\s+(\w+)/gm,
        ),
      ].map((m) => {
        const line = source.slice(0, m.index).split("\n").length;
        return {
          name: m[2],
          kind: m[1],
          line,
          snippet: lines.slice(line - 1, line + 10).join("\n"),
        };
      }),
      imports: [],
    };
  return {
    path: p,
    language: lang(p),
    category: category(p),
    lines: lines.length,
    symbols: extracted.symbols,
    imports: extracted.imports,
  };
});

const index = new Map(files.map((f) => [f.path, f]));
let anchors = 0;
function checkRefs(value) {
  if (Array.isArray(value)) {
    value.forEach(checkRefs);
    return;
  }
  if (!value || typeof value !== "object") return;
  if (
    typeof value.path === "string" &&
    typeof value.line === "number" &&
    !value.method
  ) {
    const file = index.get(value.path);
    if (!file) throw Error(`Source missing from snapshot: ${value.path}`);
    if (value.line < 1 || value.line > file.lines)
      throw Error(`Invalid source line: ${value.path}:${value.line}`);
    const symbol = (value.symbol || "").split(" / ")[0].split("（")[0].trim();
    const match =
      file.symbols.find((s) => s.name === symbol) ||
      file.symbols.find((s) => s.name.endsWith(`.${symbol}`));
    if (match) value.line = match.line;
    anchors++;
  }
  Object.values(value).forEach(checkRefs);
}
checkRefs(data);
const ids = new Set(data.nodes.map((n) => n.id));
if (ids.size !== data.nodes.length) throw Error("Duplicate node ID");
for (const edge of data.edges)
  if (!ids.has(edge.from) || !ids.has(edge.to)) throw Error("Dangling edge");
for (const feature of data.features) {
  for (const step of feature.steps) {
    if (!ids.has(step.node)) throw Error("Unknown workflow node");
    if (step.source?.symbol) {
      const symbols = index.get(step.source.path).symbols;
      const name = step.source.symbol;
      if (!symbols.some((s) => s.name === name || s.name.endsWith("." + name)))
        throw Error(`Workflow symbol not found: ${step.source.path} ${name}`);
    }
  }
  for (const test of feature.tests)
    if (!index.has(test)) throw Error(`Missing test ${test}`);
}
const routes = Object.entries(python)
  .filter(([p]) => p.startsWith("services/studio/studio/"))
  .flatMap(([file, value]) =>
    (value.routes || []).map((route) => ({ ...route, file })),
  );
data.routes = routes;
const snapshot = JSON.parse(texts["packages/contracts/openapi.json"]);
const saved = Object.entries(snapshot.paths).flatMap(([p, value]) =>
  Object.keys(value)
    .filter((m) =>
      ["get", "post", "put", "patch", "delete", "head", "options"].includes(m),
    )
    .map((m) => `${m.toUpperCase()} ${p}`),
);
const declared = routes.map((r) => `${r.method} ${r.path}`);
data.contract = {
  ...data.contract,
  snapshotPaths: Object.keys(snapshot.paths).length,
  snapshotOperations: saved.length,
  sourcePaths: new Set(routes.map((r) => r.path)).size,
  sourceOperations: declared.length,
  missingOperations: declared.filter((x) => !saved.includes(x)).sort(),
};
const projectFields = python["services/studio/studio/domain.py"].fields.Project;
data.contract.missingProjectFields = projectFields
  .filter(
    (field) =>
      !(field in (snapshot.components?.schemas?.Project?.properties || {})),
  )
  .sort();
fs.writeFileSync(
  path.join(home, "content.json"),
  JSON.stringify(data, null, 2) + "\n",
);
data.meta = {
  revision,
  date: process.argv[2] || "2026-10-29",
  branch: git(["branch", "--show-current"]),
  sourceDirty: false,
  repo: "https://github.com/Sakuranda/VTuberSongCut",
};
data.files = files;
fs.writeFileSync(
  path.join(home, "data.js"),
  "window.SONGCUT_ARCHITECTURE = " + JSON.stringify(data) + ";\n",
);
await esbuild.build({
  entryPoints: [path.join(home, "src/main.ts")],
  outfile: path.join(home, "app.js"),
  bundle: true,
  format: "iife",
  target: "es2022",
  minify: true,
  sourcemap: false,
  legalComments: "none",
});
console.log(
  JSON.stringify(
    {
      revision: revision.slice(0, 7),
      files: files.length,
      nodes: data.nodes.length,
      edges: data.edges.length,
      features: data.features.length,
      sourceAnchors: anchors,
      bytes: fs.statSync(path.join(home, "data.js")).size,
    },
    null,
    2,
  ),
);
