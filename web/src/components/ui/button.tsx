/* eslint-disable react-refresh/only-export-components -- constants/helpers exported beside components (shadcn convention) */
import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

/** Growing hover pill (design/02 section 2.3): a ::before pill inset 4px and invisible at rest
 * grows to the edge on hover/focus-visible (entry animates, exit is instant). */
const PILL =
  "relative isolate before:absolute before:inset-1 before:-z-10 before:rounded-[inherit] before:bg-foreground/[0.06] before:opacity-0 hover:before:inset-0 hover:before:opacity-100 hover:before:transition-[inset,opacity] hover:before:duration-150 hover:before:ease-out focus-visible:before:inset-0 focus-visible:before:opacity-100 data-[state=open]:before:inset-0 data-[state=open]:before:opacity-100 aria-pressed:before:inset-0 aria-pressed:before:opacity-100 motion-reduce:before:transition-none"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all disabled:pointer-events-none disabled:opacity-50 aria-[disabled=true]:cursor-not-allowed aria-[disabled=true]:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 aria-invalid:border-destructive",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-xs hover:bg-primary/90",
        destructive:
          "bg-destructive text-white shadow-xs hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40",
        outline:
          "border border-input bg-background shadow-xs hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/80",
        ghost: `${PILL} bg-transparent`,
        quiet: `${PILL} bg-transparent text-muted-foreground hover:text-foreground`,
        "destructive-ghost": `${PILL} bg-transparent text-muted-foreground hover:text-destructive before:bg-destructive/10`,
        accent: "bg-status-working text-status-working-foreground shadow-xs hover:bg-status-working/90",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-xs": "size-6",
        "icon-sm": "size-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

const Button = React.forwardRef<
  HTMLButtonElement,
  React.ComponentProps<"button"> &
    VariantProps<typeof buttonVariants> & {
      asChild?: boolean
    }
>(function Button({ className, variant, size, asChild = false, ...props }, ref) {
  const Comp = asChild ? Slot : "button"

  return (
    <Comp
      ref={ref}
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
})

export { Button, buttonVariants }
