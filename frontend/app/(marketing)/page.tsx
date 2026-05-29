import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="mx-auto max-w-3xl text-center">
        {/* Logo / Brand */}
        <div className="mb-6 inline-flex items-center gap-2">
          <div className="h-10 w-10 rounded-lg bg-primary" />
          <span className="text-2xl font-bold tracking-tight">AstraGate</span>
        </div>

        {/* Headline */}
        <h1 className="mb-4 text-5xl font-extrabold tracking-tight text-foreground sm:text-6xl">
          One API for Every LLM
        </h1>

        {/* Tagline */}
        <p className="mb-8 text-xl text-muted-foreground">
          Access OpenAI, Anthropic, Groq, DeepSeek, and more through a single
          OpenAI-compatible endpoint. Prepaid credits, no surprises.
        </p>

        {/* CTA Buttons */}
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <Link
            href="/register"
            className="inline-flex h-11 items-center justify-center rounded-md bg-primary px-8 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            Get Started Free
          </Link>
          <Link
            href="/login"
            className="inline-flex h-11 items-center justify-center rounded-md border border-input bg-background px-8 text-sm font-medium shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            Sign In
          </Link>
        </div>

        {/* Feature highlights */}
        <div className="mt-16 grid grid-cols-1 gap-6 text-left sm:grid-cols-3">
          <div className="rounded-lg border bg-card p-6 shadow-sm">
            <h3 className="mb-2 font-semibold">OpenAI-Compatible</h3>
            <p className="text-sm text-muted-foreground">
              Drop-in replacement for the OpenAI SDK. Change one line of code.
            </p>
          </div>
          <div className="rounded-lg border bg-card p-6 shadow-sm">
            <h3 className="mb-2 font-semibold">Prepaid Credits</h3>
            <p className="text-sm text-muted-foreground">
              Top up and spend. No monthly bills, no credit card required to
              start.
            </p>
          </div>
          <div className="rounded-lg border bg-card p-6 shadow-sm">
            <h3 className="mb-2 font-semibold">Start in 5 Minutes</h3>
            <p className="text-sm text-muted-foreground">
              Register, get your API key, and make your first call — all in
              under 5 minutes.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
