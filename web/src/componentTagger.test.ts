import { describe, expect, it } from "vitest"
import { tagComponentRoots } from "@/../componentTagger"

const RELATED_REPOS_SHAPE = `export function RelatedReposSelect({ mainRepo }: { mainRepo: string }) {
  const options = useMemo(() => {
    const main = mainRepo.trim().toLowerCase()
    return (repos ?? [])
      .map((r) => r.name)
      .filter((n) => n.toLowerCase() !== main)
  }, [mainRepo])

  if (options.length === 0) return null
  return (
    <div className="grid gap-2">
      <span>pick</span>
    </div>
  )
}`

describe("tagComponentRoots", () => {
  it("tags the component root once", () => {
    const out = tagComponentRoots(`export function Card() {
  return (
    <div>hi</div>
  )
}`)
    expect(out.changed).toBe(true)
    expect(out.code).toContain('<div data-component="Card">')
  })

  it("does not double-tag when a non-JSX return precedes the JSX return", () => {
    const out = tagComponentRoots(RELATED_REPOS_SHAPE)
    expect(out.changed).toBe(true)
    const hits = out.code.match(/data-component="RelatedReposSelect"/g) ?? []
    expect(hits).toHaveLength(1)
  })
})
