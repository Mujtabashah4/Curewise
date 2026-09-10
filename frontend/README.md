# CureWise — frontend

Next.js 16 (App Router), React 19, TypeScript, Tailwind v4. Talks to the
FastAPI backend in `../backend`.

## Setup

```bash
cp .env.example .env.local   # NEXT_PUBLIC_API_URL, defaults to localhost:8000
npm install
npm run dev                  # http://localhost:3000
```

The backend must be running for anything behind auth to work.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server on :3000 |
| `npm run build` | Production build (`output: "standalone"`) |
| `npm start` | Serve the production build |
| `npm run lint` | ESLint (`eslint-config-next`) |

## Layout

```
src/
  app/
    page.tsx              # marketing landing page
    login/ signup/        # auth screens
    (app)/                # everything behind auth, wrapped in AppShell
      layout.tsx          #   sidebar + mobile sheet, redirects out if signed out
      dashboard/          #   tool tiles
      chat/               #   RAG health assistant
      report/             #   blood-report upload + results table
      screening/          #   model list, and [disease]/ per-model screen + chat
      skin/               #   acne / skin photo check
      emergency/          #   nearby hospitals
      history/ profile/   #   account
    globals.css           # every design token + global classname (see ../DESIGN.md)
  components/
    ui/                   # shadcn/radix primitives, re-skinned by the tokens
    app-shell.tsx         # authed chrome
    nav-config.tsx        # single source of truth for nav + dashboard tiles
    ai-response.tsx       # renders model text (no markdown dep, never raw HTML)
  lib/
    api.ts                # typed client for every backend route
    auth.tsx              # AuthProvider / useAuth, token in localStorage
```

## Conventions

- **No component hardcodes a color.** Every color, radius, shadow and easing is
  a token in `globals.css`. Change it there and it applies everywhere.
- Two fonts only: Archivo (everything readable) and Azeret Mono (indices,
  identifiers, measurements). `--font-serif` is deliberately aliased to the sans.
- Dark mode is a `.dark` class on `<html>`, set before first paint by an inline
  script in `app/layout.tsx` and remembered in `localStorage`.
- Adding a screening model needs **no frontend change** — the screening pages
  render whatever `GET /api/disease-detection` returns. Only the cosmetic
  `MODALITY` map in `app/(app)/screening/page.tsx` wants a new entry.
- Every AI result renders the shared `<Disclaimer />`. That is a product rule,
  not a style choice — see `../PRODUCT.md`.

See `../DESIGN.md` for the visual system and `../README.md` for the whole stack.
