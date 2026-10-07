> AI-generated documentation (Claude Code, 2026-10-07). Reviewed and owned by the repository author.

# Deployment runbook: Render and MongoDB Atlas

This is a manual runbook. It describes how to deploy the application; it does **not** record that it has been deployed. The results table in [section 8](#8-verification-checklist) is left blank for the person who performs and verifies the deployment. Do not describe the deployment as working until that table is filled in from the live site.

Hosting facts in this document (free-plan behaviour, where settings are found in each dashboard) were read from the Render and MongoDB Atlas documentation on 2026-10-07 and can change. Settings names and application behaviour were read from `render.yaml`, `.env.example` and `backend/app/config.py`.

Contents

1. [What gets deployed](#1-what-gets-deployed)
2. [Before you start](#2-before-you-start)
3. [MongoDB Atlas](#3-mongodb-atlas)
4. [Backend web service](#4-backend-web-service)
5. [Frontend static site](#5-frontend-static-site)
6. [Order of operations: CORS_ORIGINS and VITE_API_BASE_URL](#6-order-of-operations-cors_origins-and-vite_api_base_url)
7. [Cold starts and other free-plan behaviour](#7-cold-starts-and-other-free-plan-behaviour)
8. [Verification checklist](#8-verification-checklist)
9. [Troubleshooting](#9-troubleshooting)
10. [Rollback](#10-rollback)
11. [Not yet verified](#11-not-yet-verified)

## 1. What gets deployed

| Piece | Where | Plan | Holds |
|---|---|---|---|
| API | Render web service, Python, root directory `backend` | Free | No data. The process keeps nothing on disk. |
| Frontend | Render static site, root directory `frontend` | Free | Static files only. No secrets. |
| Database | MongoDB Atlas cluster | Free (M0) | Sessions, sources, profiles, evidence with vectors, jobs, drafts, rate-limit counters |

The core application uses ordinary MongoDB collections and ranks vectors in Python. **No Atlas Vector Search index is needed and none should be created.** All indexes the application needs, including the TTL indexes, are created by the application itself at startup, idempotently; nothing is ever dropped.

`render.yaml` in the repository root describes both Render services. It contains no secret values.

## 2. Before you start

- [ ] The repository is on GitHub and `git status` shows that `.env`, the experience master file and `backend/fixtures/jobs/live/_local_full/` are **not** tracked (they are listed in `.gitignore`).
- [ ] The local checks in [TESTING.md](TESTING.md) pass on the commit you are about to deploy.
- [ ] You have a Render account and a MongoDB Atlas account. Nothing in this runbook needs a paid plan. Do not add paid resources.
- [ ] You have the OpenAI API key at hand, to paste into Render's dashboard. It must never be put in `render.yaml`, in a `VITE_` variable or in a commit.

## 3. MongoDB Atlas

### 3.1 Cluster

1. Create a project and a free (M0) cluster.
2. Choose the cloud region closest to the Render region the API will run in. `render.yaml` does not set a region, so a Blueprint creates the API in Render's default region, Oregon; AWS `us-west-2` is the matching Atlas region. If you pick a different Render region in the dashboard, match that instead.

### 3.2 Database user: least privilege

Atlas: **Security > Database Access > Add New Database User**.

- Authentication: password. Let Atlas generate it.
- Privileges: **not** "Atlas admin" and not "Read and write to any database". Choose specific privileges and grant the built-in role `readWrite` on the single database `resume_tailor` (the value of `MONGODB_DATABASE` in `render.yaml`).

`readWrite` on that one database is everything the application needs: reading, writing and creating indexes in its own collections. It cannot touch other databases or manage users.

Use this user only for the deployed API. If you also want to run the local backend against Atlas, create a second user for a separate development database rather than sharing the production one.

### 3.3 Network access: IP access list

Atlas refuses any connection from an address that is not on the project's IP access list (**Security > Network Access**). Each entry is one address or a CIDR range.

Add two kinds of entries:

1. **The API's outbound addresses.** In the Render dashboard open the API service's page (not the workspace home), open the **Connect** menu in the upper right and switch to the **Outbound** tab. Copy every IP range shown there into the Atlas access list. These ranges are shared by all Render services in the same region; static sites have none because they make no outbound connections.
2. **Your own current IP address**, only if you will connect to this cluster from your machine (for example with `mongosh` or Compass to inspect data). Atlas offers "Add current IP address". Consider making this entry temporary; Atlas can expire an entry automatically.

**Do not add `0.0.0.0/0` ("allow access from anywhere") by default.** It exposes the cluster to the whole internet and leaves the database password as the only protection.

Two things to know:

- The Outbound tab exists only once the service exists. So the order is: create the API service ([section 4](#4-backend-web-service)), copy its ranges, add them in Atlas. Until then the API runs but `/readyz` answers 503. That is expected: the application starts without a database and retries.
- Render's documentation states that for workspaces created before 2022-01-23, services in the Oregon region do not have a fixed set of outbound addresses. If the Outbound tab shows no ranges, create the API in a different region (and the Atlas cluster next to it). Opening the cluster to `0.0.0.0/0` is the fallback of last resort; if you do it, record it in [section 8](#8-verification-checklist) as a known deviation.

### 3.4 Connection string

Atlas: **Connect > Drivers** on the cluster. The string has this form:

```text
mongodb+srv://<user>:<password>@<cluster-host>/?retryWrites=true&w=majority
```

- Percent-encode the password if it contains characters such as `@`, `:`, `/` or `%`.
- The database name is **not** taken from this string. It comes from `MONGODB_DATABASE`.
- This string is a secret. It goes into Render's dashboard as `MONGODB_URI` and nowhere else: not into the repository, a document, a screenshot or a chat message.

## 4. Backend web service

Render: **New > Web Service**, connected to the GitHub repository.

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/healthz` |
| Instance type | Free |
| Region | the one your Atlas cluster is next to |

Never add `--reload` to the start command. `requirements.txt` is the fully pinned lockfile; `requirements-dev.txt` is not installed in production.

### Environment variables

Names only. Values for the first group are typed into the Render dashboard and exist nowhere else.

**Secrets (set in the dashboard, never committed)**

| Name | What it is |
|---|---|
| `MONGODB_URI` | The Atlas connection string from [3.4](#34-connection-string) |
| `OPENAI_API_KEY` | Used for both generation and embeddings |
| `IP_HASH_SALT` | Random value that keys the hashing of client IPs in rate-limit counters. `render.yaml` asks Render to generate it; when creating the service by hand, add it with Render's "Generate" option. If it is absent the application derives a key from `MONGODB_URI` instead. |

**Not secret, but set in the dashboard because it depends on another service**

| Name | Value |
|---|---|
| `CORS_ORIGINS` | The static site's exact origin, for example `https://<static-site-name>.onrender.com`. Scheme and host only, no path. Several origins are separated by commas. A `*` is rejected at startup. |

**Non-secret settings (values are in `render.yaml`)**

| Name | Value in `render.yaml` | Note |
|---|---|---|
| `PYTHON_VERSION` | `3.12.2` | The Python version the test suite was run with |
| `APP_ENV` | `production` | Turns on the production rules below |
| `AI_PROVIDER` | `openai` | `fake` is refused in production |
| `MONGODB_DATABASE` | `resume_tailor` | |
| `TRUST_PROXY_HEADERS` | `true` | Client IP is read from `X-Forwarded-For`, because the socket peer is Render's proxy |
| `OPENAI_MODEL` | `gpt-6-luna` | Only `gpt-6-luna` and `gpt-5.6-terra` are accepted |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `OPENAI_EMBEDDING_DIMENSIONS` | `1536` | Must match the embedding model or startup fails |
| `OPENAI_REASONING_EFFORT` | `low` | |
| `RETRIEVAL_MODE` | `python` | The only implemented value |
| `SESSION_TTL_HOURS` | `24` | |
| `MAX_PROFILE_CHARS` | `60000` | |
| `MAX_JOB_CHARS` | `25000` | |
| `PROVIDER_TIMEOUT_SECONDS` | `120` | |
| `PROVIDER_MAX_RETRIES` | `2` | |
| `SESSION_CREATE_LIMIT_PER_HOUR` | `20` | Per client IP |
| `GLOBAL_DAILY_AI_CALL_LIMIT` | `600` | Hard ceiling on provider calls per UTC day across all visitors |

Every other setting in `.env.example` (`QUOTA_*`, `MAX_SOURCES`, `MAX_REQUEST_BYTES`, `MAX_EVIDENCE_CHUNKS`, `MAX_REQUIREMENTS`, `MAX_OUTPUT_TOKENS_*`, `RETRIEVAL_*`, `ENABLE_SEMANTIC_VERIFIER`, `MONGODB_SERVER_SELECTION_TIMEOUT_MS`) has a built-in default and needs no entry unless you want a different value.

Do **not** upload the local `.env` file to Render. Use the same variable names with values entered in the dashboard.

### Production rules enforced at startup

With `APP_ENV=production` the application refuses to start, with a message that names the setting but never its value, when:

- `AI_PROVIDER` is not `openai`;
- `OPENAI_API_KEY` is missing or empty;
- `CORS_ORIGINS` is not set explicitly.

An unreachable database does **not** stop startup. `/healthz` answers 200 as soon as the process is up, so Render reports the deploy as live even when Atlas is refusing connections. **Always check `/readyz` after a deploy**; it answers 200 only when the database responds and the provider is configured.

## 5. Frontend static site

Render: **New > Static Site**, connected to the same repository.

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Build command | `npm ci && npm run build` |
| Publish directory | `dist` (relative to the root directory) |

### Environment variables

| Name | Value | Note |
|---|---|---|
| `VITE_API_BASE_URL` | The API's HTTPS URL, for example `https://<api-service-name>.onrender.com` | Public. It is compiled into the JavaScript at build time, so changing it requires a new build. |
| `NODE_VERSION` | A Node.js release you have built with locally (see the note below) | Recommended; not in `render.yaml` |

Everything with the `VITE_` prefix is shipped to every visitor's browser. `VITE_API_BASE_URL` is the only such variable this application reads. **No secret may ever be given a `VITE_` name.**

**Node version.** `frontend/package.json` declares `"engines": {"node": ">=20.19"}`. Render's documentation says an unbounded range like this resolves to the latest Node.js release, which changes over time. To get a predictable build, set `NODE_VERSION` on the static site to a specific version. The build was run locally on Node 23.10.0; no build has been verified on Render yet.

### Rewrite rule for the single-page app

The frontend uses client-side routes (`/profile`, `/job`, `/workspace/<id>`). Without a rewrite, refreshing one of them returns Render's 404 page.

Static site > **Redirects/Rewrites** > add:

| Source | Destination | Action |
|---|---|---|
| `/*` | `/index.html` | Rewrite |

The action must be **Rewrite**, not Redirect. Render does not apply the rule when a real file exists at the path, so scripts, styles and fonts are still served normally. `render.yaml` declares the same rule under `routes`.

## 6. Order of operations: `CORS_ORIGINS` and `VITE_API_BASE_URL`

Each service needs the other's URL, and both URLs are only certain once the services exist. The API will not start in production without `CORS_ORIGINS`, and the frontend bakes the API URL in at build time. This order works:

1. **Create the API service** with `MONGODB_URI`, `OPENAI_API_KEY` and the non-secret settings. For `CORS_ORIGINS` enter the URL you expect the static site to get, `https://<intended-static-site-name>.onrender.com`. Write down the API's actual URL once Render shows it.
2. **Add the API's outbound IP ranges to the Atlas access list** ([3.3](#33-network-access-ip-access-list)). Check that `/readyz` now answers 200.
3. **Create the static site** with `VITE_API_BASE_URL` set to the API's actual URL from step 1. Add the rewrite rule. Write down the static site's actual URL.
4. **Compare.** If the static site's actual origin differs from what you entered in step 1 (Render may add a suffix when a name is already taken), update `CORS_ORIGINS` on the API to the actual origin and save it with a deploy, so the running process picks it up.
5. **Verify** with [section 8](#8-verification-checklist).

Later changes:

| You changed | You must also |
|---|---|
| The API's URL (renamed or recreated service) | Update `VITE_API_BASE_URL` and **rebuild** the static site. A redeploy without a build keeps the old URL in the bundle. |
| The static site's URL, or added a custom domain | Update `CORS_ORIGINS` on the API to the exact new origin |
| Rotated the database password | Update `MONGODB_URI` on the API |
| Rotated the OpenAI key | Update `OPENAI_API_KEY` on the API. The frontend is not involved. |

CORS is not authorisation. It only tells browsers which sites may call the API; access to data is controlled by the session token.

### Using the Blueprint instead

**New > Blueprint** and selecting the repository creates both services from `render.yaml`. Render prompts once, during creation, for each variable marked `sync: false` (`MONGODB_URI`, `OPENAI_API_KEY`, `CORS_ORIGINS`, `VITE_API_BASE_URL`), and generates `IP_HASH_SALT`. The same ordering problem applies: enter the expected URLs, then correct `CORS_ORIGINS` or `VITE_API_BASE_URL` in the dashboard if the real URLs differ. On later Blueprint syncs Render ignores `sync: false` variables, so values typed into the dashboard are not overwritten.

`render.yaml` has not been validated by Render yet. If the Blueprint is rejected or a service does not come up as described, create the two services by hand with the settings in sections 4 and 5; they are the same settings.

## 7. Cold starts and other free-plan behaviour

**The API sleeps.** Render spins a free web service down after 15 minutes without inbound traffic. The next request wakes it, which takes about a minute.

How the application handles that:

- Creating a session (the first API call a new visitor makes) retries with backoff for up to about 90 seconds and shows a "waking the server" notice.
- No other call waits for a wake-up automatically. Reads retry at most twice within a few seconds. Actions such as "Extract" or "Generate" never retry on their own; after a failure the input is kept and a Retry button is shown.
- A visitor who returns to an open tab after the API has gone to sleep may therefore see one failed request and need to press Retry.

**What a restart loses.** Nothing durable: all data is in Atlas. A generation that was in flight when the process stopped stays `running` for up to five minutes; until then a retry answers `409 generation_in_progress`, and after that a retry with the same idempotency key takes the abandoned run over under the same draft ID. The retry is a new attempt: it counts against the session's generation quota and calls the provider again. Rate-limit counters and quotas are stored in MongoDB and survive restarts.

**Other free-plan limits to keep in mind** (from Render's and Atlas's documentation):

| Limit | Consequence |
|---|---|
| 750 free instance hours per workspace per month | Enough for one always-reachable free service; several free services share the budget |
| Free web service: 512 MB memory, 0.1 CPU | Generation is network-bound, so this is adequate; profile indexing and ranking are small numpy operations |
| No shell or SSH on free web services | Diagnose from the Logs tab |
| Local filesystem is lost on every restart, redeploy and spin-down | The application writes nothing to disk |
| Atlas M0: 0.5 GB storage, 500 connections, about 100 operations per second | See the storage note below |
| Atlas pauses a free cluster after 30 days without any connection | Resume it in the Atlas console before a demo |

**Storage note.** Each evidence chunk stores a 1536-number vector, and evidence of earlier profile versions is kept until the session expires (24 hours). Many sessions that each confirm a large profile repeatedly could approach the 0.5 GB limit. This has not been measured. If it becomes a concern, lower `QUOTA_CONFIRM` or `MAX_EVIDENCE_CHUNKS` through the environment; no code change is needed.

**Cost guard.** `GLOBAL_DAILY_AI_CALL_LIMIT` caps provider calls per UTC day across all visitors. When it is reached, AI actions answer `429 quota_exceeded` until the next UTC day. Set a monthly budget limit on the OpenAI account as well; the application's cap is not a billing control.

## 8. Verification checklist

Fill this in from the live deployment. Leave a row blank, or write "not verified", rather than guessing. Use fictional data only (the "Try sample profile" option is fictional).

Set these in a terminal first (the values are public URLs, not secrets):

```bash
API=https://<api-service-name>.onrender.com
WEB=https://<static-site-name>.onrender.com
```

### 8.1 Record of the deployment

| Item | Value |
|---|---|
| Date and time of verification (ET) | |
| Deployed commit (full SHA) | |
| Repository URL | |
| API URL | |
| Frontend URL | |
| Render region | |
| Atlas cluster tier and region | |
| API build status / deploy ID | |
| Static site build status / deploy ID | |
| Node version used by the static site build | |
| Environment variable **names** set on the API (no values) | |
| Environment variable **names** set on the static site (no values) | |
| Atlas access list entries (ranges only; note if `0.0.0.0/0` was used and why) | |

### 8.2 Checks

| # | Check | How | Expected | Result | Notes |
|---|---|---|---|---|---|
| 1 | Liveness | `curl -s $API/healthz` | `{"status":"ok"}` | | |
| 2 | Readiness and real provider | `curl -s $API/readyz` | HTTP 200, `"status":"ready"`, `"database":"ok"`, `"provider":"configured"`, `"provider_mode":"openai"` | | |
| 3 | CORS allows the frontend | `curl -s -i -X OPTIONS $API/api/generations -H "Origin: $WEB" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type,idempotency-key"` | 200; `access-control-allow-origin` equals `$WEB` exactly; `idempotency-key` among the allowed headers | | |
| 4 | CORS refuses another origin | same command with `-H "Origin: https://example.org"` | 400 and no `access-control-allow-origin` header | | |
| 5 | Protected route needs a token | `curl -s -o /dev/null -w '%{http_code}\n' $API/api/profile` | `401` | | |
| 6 | Not demo mode | Open `$WEB` in a private window | No "Demo mode" banner; the privacy notice names the AI provider | | |
| 7 | Full flow with the sample profile | Start > Try sample profile > submit > review > resolve the conflict > confirm > target job (sample job) > generate | A draft opens in the workspace; no step needed a manual URL | | |
| 8 | Source links | Click several evidence badges on resume bullets and coverage rows | The exact source excerpt, source label and role or project are shown | | |
| 9 | Coverage | Open the coverage panel | Counts and percentage shown; missing items say no evidence was found in the supplied profile; labelled as evidence coverage | | |
| 10 | Manual edit and revalidation | Edit a bullet, save, then Revalidate | The item shows as edited until revalidated; the status changes after Revalidate; the text is unchanged | | |
| 11 | Targeted regeneration | Regenerate one bullet | Only that item changes and it has a status | | |
| 12 | Print | Print / Save as PDF for the resume and for the cover letter | One column, selectable text, no badges, buttons or panels; sensible page breaks | | |
| 13 | Mobile | Repeat steps 7 to 10 at 375 px width (browser device toolbar or a phone) | No horizontal scrolling; tabs and the evidence sheet work | | |
| 14 | Deep link refresh | Refresh on `/profile`, `/job` and `/workspace/<id>`; also `curl -s -o /dev/null -w '%{http_code}\n' $WEB/profile` | The same screen returns; `200` | | |
| 15 | Clear my data | Clear my data, confirm | Returns to Start; the old workspace URL shows no draft | | |
| 16 | Second session isolation | In a second private window create another session; try the first session's workspace URL | "Draft not found"; neither session sees the other's profile, jobs or drafts | | |
| 17 | Persistence across restart | Note a workspace URL, restart the API from the Render dashboard, reload the page in the same tab | The draft is still there | | |
| 18 | Cold start | Leave the API idle for more than 15 minutes, then open `$WEB` in a new private window and submit the Start form | A waking notice, then the flow continues without an error page | | |
| 19 | No secret in the frontend bundle | See the commands below | No match | | |
| 20 | No secret or personal data in the repository | On GitHub, confirm `.env` and the experience master file are absent; search the repository for `sk-` and `mongodb+srv://` | Absent; no real key or connection string | | |
| 21 | No token in URLs | Watch the address bar and the browser's network panel during the flow | The session token appears only in the `Authorization` header | | |
| 22 | Logs are clean | Render > API service > Logs, after running the flow | JSON lines with request ID, route template, status, duration and token counts; no pasted text, generated text, bearer token, API key or connection string | | |
| 23 | Rate limit sees real client addresses | See [section 11](#11-not-yet-verified) | | | |

Commands for check 19:

```bash
mkdir -p /tmp/rt-bundle && cd /tmp/rt-bundle
curl -s "$WEB/" -o index.html
for f in $(grep -oE '/assets/[A-Za-z0-9._-]+\.(js|css)' index.html | sort -u); do curl -s -O "$WEB$f"; done
grep -lE 'sk-[A-Za-z0-9_-]{20,}|mongodb(\+srv)?://' * || echo "no key or connection string found"
```

The bundle is expected to contain the API's public URL and the fictional sample profile (Jordan Rivera). It must not contain text from the experience master file.

### 8.3 Anything that could not be verified

| Item | Why it was not verified |
|---|---|
| | |

## 9. Troubleshooting

Start with the **Logs** tab of the affected Render service, then check in this order: build, start, configuration, database network access, API base URL.

| Symptom | Likely cause | What to do |
|---|---|---|
| API build fails | Wrong root directory, or a Python version that cannot install the pinned packages | Root directory must be `backend`; set `PYTHON_VERSION` to `3.12.2` as in `render.yaml` |
| API deploy fails right after the build with `Invalid configuration - ...` | A production rule failed | The message names the setting: set `CORS_ORIGINS`, `OPENAI_API_KEY`, or correct `AI_PROVIDER`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_DIMENSIONS`, `RETRIEVAL_MODE` |
| API fails to start with a module import error | Wrong root directory or start command | The start command must be `uvicorn app.main:app ...` run from `backend` |
| Deploy is live, `/healthz` is 200, `/readyz` is 503 with `"database":"unavailable"` | Atlas refuses the connection | Add the API's outbound ranges to the Atlas access list; check user name, password and percent-encoding in `MONGODB_URI`; check that the cluster is not paused |
| `/readyz` shows `"provider":"not_configured"` | `OPENAI_API_KEY` is empty | Set it in the dashboard |
| API calls answer `503 database_unavailable` after working earlier | Atlas cluster paused or access list changed | Resume the cluster; re-check the access list |
| Browser console shows a CORS error; the page says it could not reach the server | `CORS_ORIGINS` does not match the frontend's origin exactly | Copy the origin from the address bar (scheme and host, no path) into `CORS_ORIGINS` |
| The frontend calls `http://127.0.0.1:8000` | `VITE_API_BASE_URL` was not set when the site was built | Set it and trigger a new build |
| Refreshing `/profile` gives a 404 page | Rewrite rule missing, or added as a redirect | Add `/*` to `/index.html` with action Rewrite |
| Static site deploys but shows a blank page or "not found" | Publish directory not found | It is `dist` relative to the root directory `frontend`; if the build log says the directory is missing, try `frontend/dist` |
| Static site build fails on a Node or dependency error | Node version chosen by Render differs from the one used locally | Set `NODE_VERSION` |
| First request after a pause takes about a minute or fails once | Free-plan cold start | Expected; see [section 7](#7-cold-starts-and-other-free-plan-behaviour) |
| `502 provider_unavailable` with "rejected this server's credentials" | Invalid or revoked OpenAI key | Replace `OPENAI_API_KEY` |
| `503 provider_rate_limited` | OpenAI rate limit or exhausted credit | Wait, or check the OpenAI account's usage and billing |
| `429 quota_exceeded` with `"scope":"global_daily"` | The application's daily AI-call cap was reached | Wait for the next UTC day or raise `GLOBAL_DAILY_AI_CALL_LIMIT` |
| `429 rate_limited` when starting | More than `SESSION_CREATE_LIMIT_PER_HOUR` sessions from one address this hour | Wait for the time in `Retry-After`; if all visitors are affected at once, see [section 11](#11-not-yet-verified) |
| `504 provider_timeout` on Generate | The provider or the whole generation was too slow | Retry (each attempt counts against the generation quota). If it keeps happening, try a lower `RETRIEVAL_MAX_CONTEXT` or the other `OPENAI_MODEL` |

Every error response carries a `request_id`, also shown in the interface. Search the API logs for it to find the matching lines.

## 10. Rollback

- **Code.** In the Render dashboard, open the service's deploy history and roll back to the last deploy that worked, or revert the commit on GitHub and let Render deploy it. Roll back the API and the static site to commits that belong together.
- **A static-site rollback serves the old bundle**, including the `VITE_API_BASE_URL` it was built with. If the API URL changed since then, rebuild instead of rolling back.
- **Database.** There are no migrations. Collections and indexes are created idempotently and never dropped, and stored documents carry their own versions, so an older release can run against the same database. All visitor data expires within `SESSION_TTL_HOURS` in any case. If a release has written documents an older release cannot read, the safe reset is to let them expire, or to delete the affected collections' documents in Atlas knowing that it ends all active sessions.
- **Secrets.** If a key or connection string may have been exposed, rotate it at the provider (OpenAI, Atlas), update the value in Render, and only then investigate. Removing it from a later commit is not enough.
- **Configuration.** An environment variable takes effect on the next deploy of the API, and on the next build of the static site. To undo a change, set the previous value again and deploy.

## 11. Not yet verified

These points could not be established from the code or the documentation alone and need to be observed on the deployed service. Until they are, treat them as open.

| Point | Why it matters | How to check |
|---|---|---|
| `render.yaml` is accepted by Render as written | It has not been run through Render | Create the Blueprint, or create the services by hand with the same settings |
| Which Node version the static site builds with | `engines` is an unbounded range | Read it in the build log; pin with `NODE_VERSION` |
| How Render's proxy fills `X-Forwarded-For` | With `TRUST_PROXY_HEADERS=true` the API takes the **first** entry as the client address for the per-IP session limit. If the proxy appends to a header supplied by the client, a client could choose its own "address" and evade the limit; if every visitor appears under one proxy address, they would share one limit. | Send `curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/sessions -H "X-Forwarded-For: 203.0.113.9"` more than `SESSION_CREATE_LIMIT_PER_HOUR` times within an hour and see whether `429` appears. Each call creates an empty session that expires by itself. |
| The whole flow against the real OpenAI models on the deployed service | Prompt behaviour, output-token limits and latency for `gpt-6-luna` under the 120-second per-call and 270-second per-generation limits | Checks 6 to 11 in section 8 |
| Behaviour of a returning visitor after a cold start | Only session creation retries through a wake-up | Check 18, plus reloading an existing workspace after the API has slept |
| Storage growth on the free Atlas tier | See the storage note in section 7 | Atlas > cluster > metrics after real use |
| `/docs`, `/redoc` and `/openapi.json` are publicly reachable | They expose the API's shape, not data or secrets; decide whether that is acceptable for a public deployment | Open `$API/docs` |
