# Deployment

How `.github/workflows/cd.yml` gets this pipeline running on Render. See D8 and
D9 in [`DECISIONS.md`](../DECISIONS.md) for why Render's free tier and an
ephemeral KPI store were accepted.

---

## What CI does automatically

On every push to `main`:
1. Builds the image from the repo's `Dockerfile`.
2. Pushes it to `ghcr.io/<owner>/surgical-fhir-pipeline`, tagged `latest` and
   with the commit SHA.
3. If the `RENDER_DEPLOY_HOOK_URL` repo secret is set, POSTs to it with
   `imgURL=<the digest just pushed>` so Render redeploys that exact image.
   If the secret is not set, this step is skipped and only logs a notice —
   the image still gets pushed to GHCR.

## What you do once, by hand

These steps can't be done from here — they need the Render dashboard and
GitHub repo settings UI:

1. **Make the GHCR package public.** After the first CD run pushes an image,
   go to the package's GitHub page → Package settings → change visibility to
   Public. A public image can be pulled without registry credentials; a
   private one would need a token with `read:packages` configured in Render.
2. **Create the Render service.** In the Render dashboard: New → Web Service →
   "Deploy an existing image" → `ghcr.io/<owner>/surgical-fhir-pipeline:latest`,
   free instance type. No port setting is needed: Render supplies `PORT`
   (default 10000) and the container binds to it, falling back to 8000 when
   `PORT` is unset (local runs).
3. **Create the `RENDER_DEPLOY_HOOK_URL` repository secret.** Copy the deploy
   hook URL from the new service's Settings tab and add it as a secret named
   `RENDER_DEPLOY_HOOK_URL` under the repo's Settings → Secrets and variables →
   Actions. Treat this URL as a bearer credential: anyone who has it can
   trigger a deploy of this service.

No other secret is required. `GITHUB_TOKEN` (automatically provided to every
workflow run, with `packages: write` set in `cd.yml`) is what authenticates
the push to GHCR — you do not create or configure it.

## Known limitations of this deployment (see D8, D9)

- Free-tier instance spins down after 15 minutes idle; the next request pays
  a roughly one-minute cold start.
- The filesystem is ephemeral: the KPI SQLite ledger (`SURGICAL_FHIR_KPI_DB`)
  resets on every redeploy, restart, or spin-down. Trend history does not
  survive across these events.
- The API remains unauthenticated and read-only, matching this repo's
  existing scope (`ARCHITECTURE.md §6`, `SECURITY.md`) — this deployment does
  not change that posture.
