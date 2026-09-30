import type { Plugin } from "vite"

//
// Dev-only `data-component` tags on the root element(s) of each component's
// returned JSX — the Vue-style `data-vue-component` equivalent for
// investigating page structure in browser devtools.
//
// Mechanism: the outermost JSX element(s) of every `return` inside a
// function whose name starts with an uppercase letter get
// `data-component="<FunctionName>"`. Lowercase helpers, fragments
// (`<>…</>` — no DOM node to tag), non-JSX returns, and elements that
// already carry a hand-written `data-component` are skipped. The attribute
// is inserted right after the tag name so existing props are untouched.
//
// Scope: only `src` tsx files under this project, only when `vite` runs the
// dev server (apply serve — production `vite build` output has zero trace).
// Test files are skipped so selectors never depend on the attribute.
export function componentTagger(): Plugin {
  return {
    name: "workagent-component-tagger",
    apply: "serve",
    enforce: "pre",
    transform(code, id) {
      if (!id.endsWith(".tsx")) return null
      if (id.includes(".test.")) return null
      if (!id.includes("/src/")) return null
      const out = tagComponentRoots(code)
      if (!out.changed) return null
      return { code: out.code, map: null }
    },
  }
}

interface Span {
  name: string
  start: number
  end: number
}

// Tag root JSX elements of uppercase-named function returns. Pure string scan.
export function tagComponentRoots(code: string): { code: string; changed: boolean } {
  // Function component boundaries: `function Name(` / `const Name = (` /
  // `= function Name(` / `const Name = (args) => {`. Arrow bodies without
  // braces have no `return`, so resolving the first `{` after the match is
  // sufficient.
  const fnRe =
    /(?:function\s+([A-Z][A-Za-z0-9]*)|(?:const|let|var)\s+([A-Z][A-Za-z0-9]*)\s*=\s*(?:async\s*)?(?:function\s*)?\(|=\s*function\s+([A-Z][A-Za-z0-9]*)\s*\()/g
  const spans: Span[] = []
  let m: RegExpExecArray | null
  while ((m = fnRe.exec(code)) !== null) {
    const name = m[1] ?? m[2] ?? m[3]
    if (!name) continue
    // Skip the parameter list — destructured params like `({x})` contain
    // braces that are not the body. Walk parens from the first `(` at/after
    // the match end, then take the first `{` after the params (past `=>`).
    const paren = code.indexOf("(", m.index + m[0].length - 1)
    let searchFrom = m.index + m[0].length
    if (paren !== -1) {
      const paramsEnd = matchParen(code, paren)
      if (paramsEnd === -1) continue
      searchFrom = paramsEnd
    }
    const bodyStart = code.indexOf("{", searchFrom)
    if (bodyStart === -1) continue
    const end = matchBrace(code, bodyStart)
    if (end === -1) continue
    spans.push({ name, start: bodyStart, end })
  }
  if (spans.length === 0) return { code, changed: false }

  const edits: { pos: number; name: string }[] = []
  const retRe = /return\s*[(<]/g
  while ((m = retRe.exec(code)) !== null) {
    // Innermost owning component: last span starting at/before pos that
    // still encloses it.
    let owner: Span | null = null
    for (const s of spans) {
      if (s.start <= m.index && m.index < s.end) {
        if (!owner || s.start >= owner.start) owner = s
      }
    }
    if (!owner) continue
    const root = firstJsxTag(code, retRe.lastIndex - 1)
    if (!root || root.fragment) continue
    if (hasDataComponent(code, root.end)) continue
    edits.push({ pos: root.end, name: owner.name })
  }
  if (edits.length === 0) return { code, changed: false }
  // Insert back-to-front so earlier offsets stay valid.
  let out = code
  for (const e of edits.sort((a, b) => b.pos - a.pos)) {
    out = `${out.slice(0, e.pos)} data-component="${e.name}"${out.slice(e.pos)}`
  }
  return { code: out, changed: true }
}

// First `<Tag` after `from`: tag-name end offset + fragment flag.
function firstJsxTag(code: string, from: number): { end: number; fragment: boolean } | null {
  const lt = code.indexOf("<", from)
  if (lt === -1) return null
  const next = code[lt + 1] ?? ""
  if (next === ">") return { end: lt + 1, fragment: true }
  if (next === "/" || next === "!" || next === "?") return null
  const name = /^[A-Za-z][A-Za-z0-9.]*/.exec(code.slice(lt + 1))
  if (!name) return null
  return { end: lt + 1 + name[0].length, fragment: false }
}

// True when the tag ending at `tagEnd` already has a data-component attr.
function hasDataComponent(code: string, tagEnd: number): boolean {
  let depth: string | null = null
  for (let i = tagEnd; i < code.length; i++) {
    const c = code[i]
    if (depth) {
      if (c === "\\") {
        i += 1
        continue
      }
      if (c === depth) depth = null
      continue
    }
    if (c === '"' || c === "'") {
      depth = c
      continue
    }
    if (c === ">") return false
    if (code.startsWith("data-component", i)) return true
  }
  return false
}
// Index just past the paren matching the `(` at `open`. -1 when unbalanced.
// Nested `{...}` / `[...]` blocks inside the params are skipped as units.
function matchParen(code: string, open: number): number {
  let depth = 0
  let str: string | null = null
  let i = open
  while (i < code.length) {
    const c = code[i]
    if (str) {
      if (c === "\\") {
        i += 2
        continue
      }
      if (c === str) str = null
      i += 1
      continue
    }
    if (c === '"' || c === "'" || c === "`") {
      str = c
      i += 1
      continue
    }
    if (c === "(") depth += 1
    else if (c === ")") {
      depth -= 1
      if (depth === 0) return i + 1
    } else if (c === "{" || c === "[") {
      i = skipBalanced(code, i)
      continue
    }
    i += 1
  }
  return -1
}

// Index just past the bracketed block starting at `open` (`{`/`[`/`(`).
function skipBalanced(code: string, open: number): number {
  const pairs: Record<string, string> = { "{": "}", "[": "]", "(": ")" }
  const close = pairs[code[open]]
  let depth = 0
  let str: string | null = null
  let i = open
  while (i < code.length) {
    const c = code[i]
    if (str) {
      if (c === "\\") {
        i += 2
        continue
      }
      if (c === str) str = null
      i += 1
      continue
    }
    if (c === '"' || c === "'" || c === "`") {
      str = c
      i += 1
      continue
    }
    if (c === code[open]) depth += 1
    else if (c === close) {
      depth -= 1
      if (depth === 0) return i + 1
    }
    i += 1
  }
  return i
}

// Index just past the brace matching the `{` at `open`. -1 when unbalanced.
function matchBrace(code: string, open: number): number {
  let depth = 0
  let str: string | null = null
  let i = open
  while (i < code.length) {
    const c = code[i]
    if (str) {
      if (c === "\\") {
        i += 2
        continue
      }
      if (c === str) str = null
      i += 1
      continue
    }
    if (c === '"' || c === "'" || c === "`") {
      str = c
      i += 1
      continue
    }
    if (c === "{") depth += 1
    else if (c === "}") {
      depth -= 1
      if (depth === 0) return i + 1
    }
    i += 1
  }
  return -1
}
