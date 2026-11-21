import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
    <Card className="mb-4 border-sky-500/40" data-testid="question-card">
      <CardHeader>
        <CardTitle className="text-base">Agent question</CardTitle>
        <CardDescription>{question.text}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {question.options.length > 0 ? (
          <div className="flex flex-wrap gap-2" role="group" aria-label="Answer options">
            {question.options.map((o) => {
              const active = question.multi ? picked.includes(o) : choice === o
              return (
                <Button
                  key={o}
                  variant={active ? "default" : "outline"}
                  size="sm"
                  disabled={submit.isPending}
                  aria-pressed={question.multi ? active : undefined}
                  onClick={() =>
                    question.multi
                      ? setPicked((prev) => (prev.includes(o) ? prev.filter((x) => x !== o) : [...prev, o]))
                      : setChoice(o)
                  }
                >
                  {o}
                </Button>
              )
            })}
          </div>
        ) : null}
        <div className="flex items-center gap-2">
          <Label className="sr-only" htmlFor="acp-answer">Answer</Label>
          <Input
            id="acp-answer"
            placeholder={question.options.length > 0 ? "Or type a custom answer…" : "Type your answer…"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSend && !submit.isPending) submit.mutate()
            }}
          />
          <Button size="sm" disabled={!canSend || submit.isPending} onClick={() => submit.mutate()}>
            <Send aria-hidden /> Send
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
