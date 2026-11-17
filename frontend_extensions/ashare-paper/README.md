# Official frontend paper extension

This overlay adds `/#/ashare-paper` to the official QuantDinger Vue application,
including its navigation, JWT requests and existing localization system. It does
not replace the application with a separate unauthenticated dashboard.

The frontend is independently licensed under the source-available terms in
`UPSTREAM_LICENSE.txt`, not the backend Apache license. The pinned revision is
recorded in `manifest.json`. Preserve upstream branding, attribution and license;
commercial use needs the licensor's separate permission. This integration is for
the requested non-commercial research and paper evaluation.

From the backend repository root:

```bash
python scripts/frontend/prepare_ashare_frontend.py ../work/quantdinger-frontend --build
```

The script clones the official source if absent, verifies its revision and
license, applies the small router/localization patch and copies the new files.
It refuses unexpected upstream changes or independently modified overlay files.
It installs locked dependencies without package lifecycle hooks and builds the
complete official application. `dist/` remains outside this repository.

For local development with the backend on port 5017:

```bash
cd ../work/quantdinger-frontend
VITE_DEV_PROXY_TARGET=http://127.0.0.1:5017 pnpm dev --host 127.0.0.1 --port 5177
```

Open `http://127.0.0.1:5177/#/ashare-paper` and sign in using the official login.
The page polls persisted worker state every three seconds. The independent
managed worker must run on the server; page polling never executes orders.
Order controls require fresh quotes and a healthy worker, while server-side
checks remain authoritative. The page includes daily review and optional Feishu
setup/test instructions. Saved bot secrets are never fetched back into forms.

When updating upstream, review a new pinned revision, regenerate the patch and
verify build, authentication, actual page behavior and API contract tests before
deploying it. Do not apply the overlay blindly to another frontend version.
