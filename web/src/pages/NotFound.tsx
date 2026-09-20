import { Link } from "react-router-dom"
import { Compass } from "lucide-react"
import { Button } from "@/components/ui/button"

export function NotFound() {
  return (
    <div className="flex flex-col items-center gap-4 py-20 text-center">
      <Compass aria-hidden className="size-10 text-muted-foreground" />
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        The page you are looking for does not exist. Sessions live on the
        Dashboard, runs under Runs.
      </p>
      <Button asChild size="sm">
        <Link to="/">Back to Dashboard</Link>
      </Button>
    </div>
  )
}
