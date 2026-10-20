import * as React from "react"
import { Label } from "@/components/ui/label"

export type ScriptLang = "bash" | "python" | "powershell"

export function detectScriptLang(code: string): ScriptLang {
  const firstLine = (code.split("\n", 1)[0] ?? "").trim().toLowerCase()
  if (!firstLine.startsWith("#!")) return "bash"
  if (firstLine.includes("python")) return "python"
  if (firstLine.includes("pwsh") || firstLine.includes("powershell")) return "powershell"
  return "bash"
}

const KEYWORDS: Record<ScriptLang, string[]> = {
  bash: [
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "function", "select", "time", "coproc",
    "exit", "return", "export", "local", "readonly", "declare", "source",
    "echo", "printf", "read", "cd", "pushd", "popd", "test", "set", "unset",
    "shift", "exec", "eval", "trap", "wait", "true", "false",
  ],
  python: [
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "true", "false", "none",
  ],
  powershell: [
    "function", "param", "begin", "process", "end", "if", "else", "elseif",
    "switch", "foreach", "for", "while", "do", "until", "break", "continue",
    "return", "exit", "try", "catch", "finally", "throw", "in", "filter",
    "true", "false", "null",
  ],
}

const TOKEN_CLASS: Record<string, string> = {
  string: "text-amber-700 dark:text-amber-300",
  comment: "text-muted-foreground italic",
  variable: "text-sky-700 dark:text-sky-300",
  number: "text-violet-700 dark:text-violet-300",
  keyword: "text-emerald-700 dark:text-emerald-300",
  cmdlet: "text-cyan-700 dark:text-cyan-300",
  decorator: "text-fuchsia-700 dark:text-fuchsia-300",
}

// Dependency-free regex tokenizer. Runs per line so `#` comments only
// start a comment outside of a string. Returns React spans (no innerHTML).
function tokenizeLine(line: string, lang: ScriptLang, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  let i = 0
  let key = 0
  const push = (text: string, cls?: string) => {
    out.push(
      cls ? (
        <span key={`${keyPrefix}-${key++}`} className={cls}>{text}</span>
      ) : (
        <span key={`${keyPrefix}-${key++}`}>{text}</span>
      ),
    )
  }
  while (i < line.length) {
    const s = line.slice(i)
    // Strings (single, double, backtick). No multi-line handling: per-line.
    if (s[0] === '"' || s[0] === "'" || s[0] === "`") {
      const q = s[0]
      let j = 1
      while (j < s.length) {
        if (s[j] === "\\") { j += 2; continue }
        if (s[j] === q) { j += 1; break }
        j += 1
      }
      push(s.slice(0, j), TOKEN_CLASS.string)
      i += j
      continue
    }
    // `#` comment to end of line (shebang line counts as a comment).
    if (s[0] === "#") {
      push(s, TOKEN_CLASS.comment)
      break
    }
    // `$variables` (bash/powershell) — ${braced} and $name forms.
    if (s[0] === "$" && lang !== "python") {
      const m = /^\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)|^\$[0-9*#?$!@_-]/.exec(s)
      if (m) {
        push(m[0], TOKEN_CLASS.variable)
        i += m[0].length
        continue
      }
    }
    // `@decorators` (python).
    if (s[0] === "@" && lang === "python") {
      const m = /^@[A-Za-z_][A-Za-z0-9_.]*/.exec(s)
      if (m) {
        push(m[0], TOKEN_CLASS.decorator)
        i += m[0].length
        continue
      }
    }
    // Numbers.
    const num = /^(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)/.exec(s)
    if (num) {
      push(num[0], TOKEN_CLASS.number)
      i += num[0].length
      continue
    }
    // Words: keywords, powershell `Verb-Noun` cmdlets, else plain.
    // bash keywords match exactly (all lowercase); python/powershell match
    // case-insensitively.
    const word = /^[A-Za-z_][A-Za-z0-9_-]*/.exec(s)
    if (word) {
      const w = word[0]
      const isKeyword = lang === "bash"
        ? KEYWORDS.bash.includes(w)
        : KEYWORDS[lang].includes(w.toLowerCase())
      if (isKeyword) {
        push(w, TOKEN_CLASS.keyword)
      } else if (lang === "powershell" && /^[A-Z][A-Za-z0-9]*-[A-Z][A-Za-z0-9]*/.test(w)) {
        push(w, TOKEN_CLASS.cmdlet)
      } else {
        push(w)
      }
      i += w.length
      continue
    }
    // Single plain char (whitespace, operators, punctuation).
    push(s[0] ?? "")
    i += 1
  }
  return out
}

export function ScriptBlock({ code, maxH }: { code: string; maxH?: string }) {
  const lang = detectScriptLang(code)
  const lines = code.split("\n")
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-center gap-2 border-b bg-muted/50 px-2 py-1">
        <span className="rounded border bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {lang}
        </span>
      </div>
      <pre className={`${maxH ?? "max-h-48"} overflow-auto p-2 font-mono text-xs whitespace-pre-wrap break-words`}>
        {lines.map((line, li) => (
          <span key={li}>
            {line.length > 0 ? tokenizeLine(line, lang, `l${li}`) : " "}
            {li < lines.length - 1 ? "\n" : null}
          </span>
        ))}
      </pre>
    </div>
  )
}

export function ScriptField({
  id,
  label,
  value,
  onChange,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        value={value}
        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={3}
        spellCheck={false}
        className="flex min-h-18 w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-[13px] shadow-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
      />
    </div>
  )
}
