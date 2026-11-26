import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { QueryClientProvider } from "@tanstack/react-query"
import { queryClient } from "./lib/query"
import { ThemeProvider } from "./lib/theme"
import { ConfirmProvider } from "./lib/confirm"
import { Toaster } from "./components/ui/sonner"
import { TooltipProvider } from "./components/ui/tooltip"
import App from "./App"

import "@fontsource-variable/inter"
import "@fontsource-variable/jetbrains-mono"
import "./index.css"

const rootElement = document.getElementById("root")
if (!rootElement) throw new Error("Root element #root is missing")

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider>
          <ConfirmProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
            <Toaster position="bottom-right" />
          </ConfirmProvider>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
)
