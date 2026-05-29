# AstraGate — Frontend (Next.js Dashboard)

This directory contains the Next.js 14 frontend for AstraGate, serving both the Customer Dashboard and the Admin Dashboard.

## Tech Stack

- **Next.js 14** (App Router)
- **TypeScript**
- **Tailwind CSS**
- **shadcn/ui** — component library
- **Recharts** — usage charts

## Route Structure (to be created in Task 5)

```
frontend/
├── app/
│   ├── (marketing)/
│   │   └── page.tsx              # Landing page
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── dashboard/
│   │   ├── layout.tsx            # Customer layout + nav
│   │   ├── page.tsx              # Overview
│   │   ├── keys/page.tsx         # API Keys management
│   │   ├── usage/page.tsx        # Usage charts + table
│   │   ├── billing/page.tsx      # Top-up + transaction history
│   │   └── quickstart/page.tsx   # Quick Start guide
│   └── admin/
│       ├── layout.tsx            # Admin layout + nav
│       ├── page.tsx              # System overview
│       ├── customers/page.tsx
│       ├── providers/page.tsx
│       ├── models/page.tsx
│       └── guardrails/page.tsx
├── components/
│   ├── ui/                       # shadcn/ui components
│   ├── charts/                   # Recharts wrappers
│   └── shared/
└── lib/
    ├── api.ts                    # API client (fetch wrapper + JWT injection)
    └── auth.ts                   # JWT storage and refresh logic
```

## Getting Started

```bash
# From the repo root:
make dev   # Starts the dashboard at http://localhost:3000
```
