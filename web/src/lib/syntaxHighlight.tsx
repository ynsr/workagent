import React from "react"

export type TokenKind = "plain" | "keyword" | "string" | "comment" | "number" | "key" | "bool"

export interface SyntaxToken {
  kind: TokenKind
  text: string
}

const TOKEN_CLASS: Record<TokenKind, string> = {
  plain: "",
  keyword: "text-violet-600 dark:text-violet-400 font-medium",
  string: "text-emerald-700 dark:text-emerald-400",
  comment: "text-muted-foreground italic",
  number: "text-amber-700 dark:text-amber-300",
  key: "text-sky-700 dark:text-sky-300 font-medium",
  bool: "text-orange-600 dark:text-orange-400 font-medium",
}

const CODE_KEYWORDS = new Set([
  "async", "await", "break", "case", "catch", "class", "const", "continue",
  "def", "default", "delete", "elif", "else", "enum", "export", "extends",
  "finally", "fn", "for", "from", "func", "function", "if", "implements",
  "import", "in", "interface", "let", "match", "new", "package", "private",
  "protected", "pub", "public", "raise", "readonly", "return", "self",
  "static", "struct", "switch", "this", "throw", "try", "type", "typeof",
  "use", "var", "void", "while", "with", "yield",
])

const CODE_BOOLS = new Set(["true", "false", "null", "undefined", "None", "True", "False"])

const CODE_RE =
  /(\/\/.*$|#.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b)/g

export function tokenizeYamlLine(line: string): SyntaxToken[] {
  const trimmed = line.trimStart()
  if (trimmed.startsWith("#")) {
    return [{ kind: "comment", text: line }]
  }
  const tokens: SyntaxToken[] = []
  // Match optional leading spaces + optional list dash + key:
  const keyMatch = /^(\s*(?:-\s+)?)([A-Za-z0-9_.-]+)(\s*:)(.*)$/.exec(line)
  if (keyMatch) {
    const [, prefix = "", key = "", colon = "", rest = ""] = keyMatch
    if (prefix) tokens.push({ kind: "plain", text: prefix })
    tokens.push({ kind: "key", text: key })
    tokens.push({ kind: "plain", text: colon })
    if (rest) tokens.push(...tokenizeYamlValue(rest))
    return tokens
  }
  return tokenizeYamlValue(line)
}

function tokenizeYamlValue(val: string): SyntaxToken[] {
  const commentIdx = val.search(/\s+#/)
  const main = commentIdx >= 0 ? val.slice(0, commentIdx) : val
  const comment = commentIdx >= 0 ? val.slice(commentIdx) : ""
  const out: SyntaxToken[] = []
  const trimmed = main.trim()
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    out.push({ kind: "string", text: main })
  } else if (/^(true|false|null|~)$/i.test(trimmed)) {
    out.push({ kind: "bool", text: main })
  } else if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) {
    out.push({ kind: "number", text: main })
  } else if (main) {
    out.push({ kind: "plain", text: main })
  }
  if (comment) out.push({ kind: "comment", text: comment })
  return out
}

export function tokenizeCodeLine(line: string, path?: string): SyntaxToken[] {
  if (path && /\.(ya?ml)$/i.test(path)) {
    return tokenizeYamlLine(line)
  }
  const tokens: SyntaxToken[] = []
  let last = 0
  CODE_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = CODE_RE.exec(line)) !== null) {
    if (match.index > last) {
      tokens.push({ kind: "plain", text: line.slice(last, match.index) })
    }
    const text = match[0]
    let kind: TokenKind = "plain"
    if (text.startsWith("//") || text.startsWith("#")) kind = "comment"
    else if (text.startsWith('"') || text.startsWith("'") || text.startsWith("`")) kind = "string"
    else if (/^\d/.test(text)) kind = "number"
    else if (CODE_BOOLS.has(text)) kind = "bool"
    else if (CODE_KEYWORDS.has(text)) kind = "keyword"
    tokens.push({ kind, text })
    last = match.index + text.length
  }
  if (last < line.length) {
    tokens.push({ kind: "plain", text: line.slice(last) })
  }
  return tokens
}

export function HighlightedLine({ text, path }: { text: string; path?: string }) {
  const tokens = React.useMemo(() => tokenizeCodeLine(text, path), [text, path])
  return (
    <>
      {tokens.map((t, i) =>
        t.kind === "plain" ? (
          <React.Fragment key={i}>{t.text}</React.Fragment>
        ) : (
          <span key={i} className={TOKEN_CLASS[t.kind]}>
            {t.text}
          </span>
        ),
      )}
    </>
  )
}
