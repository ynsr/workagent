import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Pressable } from "@/components/ui/pressable"
import { StatusGlyph } from "@/components/StatusGlyph"
import { errorText } from "@/components/StatusFeedback"
import { api, type PendingQuestion } from "@/lib/api"
import { queryKeys } from "@/lib/queries"

/** Pinned ACP question card (issue #112): options + free text → POST input. */
export function QuestionCard({ runId, question }: { runId: string; question: PendingQuestion }) {
  const qc = useQueryClient()
  const [text, setText] = useState("")
  const [choice, setChoice] = useState<string>("")
  const [picked, setPicked] = useState<string[]>([])
  const answer = text.trim() || (question.multi ? picked.join(", ") : choice)
  const submit = useMutation({
    mutationFn: () => api.submitRunInput(runId, { text: answer, qid: question.qid }),
    onSuccess: () => {
      setText("")
      setChoice("")
      setPicked([])
      toast.success("Answer sent")
      void qc.invalidateQueries({ queryKey: queryKeys.run(runId) })
    },
    onError: (err: unknown) => toast.error(errorText(err)),
  })

  const canSend = Boolean(answer)
  return (
    <Card className="mb-4" data-testid="question-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-[15px]">
          <StatusGlyph tone="needs-you" />
          Agent question
        </CardTitle>
        <CardDescription>{question.text}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {/* One shared list: rows with dividers, "Type your own answer" last (design/03 section 4). */}
        <div className="divide-y divide-border overflow-hidden rounded-md bg-secondary/60" role="group" aria-label="Answer options">
          {question.options.map((o) => {
            const active = question.multi ? picked.includes(o) : choice === o
            return (
              <Pressable
                key={o}
                disabled={submit.isPending}
                aria-pressed={active}
                className="flex min-h-8 w-full items-center justify-between px-3 py-1.5 text-[13px]"
                onClick={() =>
                  question.multi
                    ? setPicked((prev) => (prev.includes(o) ? prev.filter((x) => x !== o) : [...prev, o]))
                    : setChoice(o)
                }
              >
                <span>{o}</span>
                {active ? <Check aria-hidden className="size-3.5" /> : null}
              </Pressable>
            )
          })}
          <div className="px-3 py-1">
            <Label className="sr-only" htmlFor="acp-answer">
              Answer
            </Label>
            <Input
              id="acp-answer"
              className="h-7 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
              placeholder="Type your own answer"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSend && !submit.isPending) submit.mutate()
              }}
            />
          </div>
        </div>
        <div className="flex justify-end">
          <Button
            size="sm"
            disabled={!canSend || submit.isPending}
            title={canSend ? undefined : "Pick or type an answer first"}
            onClick={() => submit.mutate()}
          >
            <Send aria-hidden /> Send answer
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
