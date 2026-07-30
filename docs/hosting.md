# Hosting wiseau so any LLM can use it

A step-by-step guide to putting wiseau on the public internet and connecting an
LLM to it. Written for someone who has not deployed a service before: every step
is a literal click or a copy-pasteable command, and the reasoning behind each
setting is stated so you can tell when something has gone wrong.

Companion documents: [`mcp.md`](mcp.md) describes the agent-facing tools
themselves; [`tech-spec.md`](tech-spec.md) is the contract; ADR-029 in
[`decisions.md`](decisions.md) records why it is built this way.

> **A note on the clicks.** Google, Render and Anthropic all move their buttons
> around. The *order of operations* below is stable — pick a host, build the
> Dockerfile, allow public access, copy the URL, paste it into a client — even
> when a label has been renamed. Where a label matters, the guide says what the
> setting *does*, so you can find its new name.

---

## 1. What you are actually building

One container. Not two, not three.

```
                    ┌──────────────────────────────────────────┐
   your browser ───►│  https://wiseau-xxxx.run.app             │
                    │                                          │
   Claude / any ───►│   /convert/url  /convert/file   REST API │
   other LLM        │   /openapi.json                 schema   │
                    │   /mcp                          MCP      │◄── connector URL
                    │   /ping  /metrics               health   │
                    └──────────────────────────────────────────┘
```

The deployed backend serves the MCP endpoint itself (ADR-029), so **the one URL
you get from your host is everything**: the API your web UI calls, the OpenAPI
schema a function-calling agent reads, and the MCP connector URL you paste into
an LLM. There is no separate MCP server to deploy, no second port, no second
bill.

### The three words you need

- **MCP** (Model Context Protocol) is a standard way for an LLM to discover and
  call tools. wiseau exposes three: `convert_url`, `convert_file`, `ping`.
- **A remote (or "hosted") MCP server** is one the LLM reaches over HTTP at a
  URL, rather than by launching a program on your own laptop. That is what makes
  it usable by an LLM running in someone else's cloud.
- **A connector** is what Claude (and ChatGPT) call the thing you create when you
  paste that URL into their settings. Same idea, vendor's name for it.

So the whole job is: **get a public HTTPS URL, then paste `<that URL>/mcp` into
an LLM's connector settings.** Everything below is in service of those two steps.

---

## 2. Pick a host

You have three free options. They differ mainly in memory, which matters because
wiseau runs a headless Chromium to render web pages, and Chromium is hungry.

| Host | Free allowance | RAM | Sleeps? | Best for |
| ---- | -------------- | --- | ------- | -------- |
| **Google Cloud Run** | ~2M requests + ~50 h of request-processing/month at 2 GiB (see §4) | you choose — use **2 GiB** | Scales to zero; ~10–30 s cold start | **Recommended.** You already use Google Cloud; it is the best fit and genuinely free at personal volume. |
| **Render** | 750 instance-hours/month | **512 MB, fixed** | Sleeps after 15 min idle; ~50 s cold start | Simplest UI. But 512 MB is tight for Chromium — expect URL rendering to fail under memory pressure. Document uploads are fine. |
| **Hugging Face Spaces** | Unlimited on the free CPU tier | **16 GB** | Pauses after long inactivity | The most memory for free, and the repo is already set up for it (`backend/README.md` carries the Space card). Best if you later enable docling. |

**Take Cloud Run.** It is the one you have, it scales to zero so idle costs
nothing, and 2 GiB is enough headroom for the browser. §9 covers Render and §10
covers Spaces if you would rather use those.

A billing account is required on Google Cloud even to use the free tier. §6 sets
up a budget alert so you find out immediately if anything ever bills.

---

## 3. Before you start

You need:

- A Google account.
- The wiseau repository on GitHub (you have it:
  `Thomas-Amann-IPAustralia/wiseau`), with this branch merged to `main`.
- A payment method for the Google Cloud billing account. **Nothing here bills at
  personal volume**, but Google requires a card on file to unlock the free tier.
- About 20 minutes, most of it waiting for the first build.

You do **not** need Docker installed, a domain name, an SSL certificate, or any
networking knowledge. Cloud Build builds the image in the cloud from the
`Dockerfile` already in this repo, and Cloud Run terminates HTTPS for you.

---

## 4. Deploy to Cloud Run — click by click

### 4.1 Create a project

1. Go to **<https://console.cloud.google.com>** and sign in.
2. At the top of the page, click the **project picker** (the dropdown next to
   "Google Cloud", showing the current project name).
3. Click **New project** (top right of the dialog).
4. **Project name:** `wiseau`. Leave the organisation/location as offered.
5. Click **Create**. Wait ~10 seconds, then use the project picker again to
   **select `wiseau`**. Everything after this must happen inside that project —
   check the name at the top of the page if a later step looks wrong.

### 4.2 Attach billing

1. In the search bar at the top, type **`Billing`** and open it.
2. If it says the project has no billing account: click **Link a billing
   account** → **Create billing account** (or select an existing one) and follow
   the prompts to add a card.
3. Confirm the page ends up showing `wiseau` linked to a billing account.

*(Why: Cloud Run's free tier is applied as a discount on a billing account. No
billing account, no free tier — and no deploy.)*

### 4.3 Create the service

1. In the search bar, type **`Cloud Run`** and open it.
2. If prompted to **Enable** the Cloud Run API, click it and wait.
3. Click **Deploy container** → **Service** (newer consoles: the **Create
   service** button).

You now have a form. Fill it in as follows.

### 4.4 Point it at your GitHub repository

1. Choose **Continuously deploy from a repository (source or function)** —
   *not* "Deploy one revision from an existing container image".
2. Click **Set up with Cloud Build**.
3. **Repository provider:** GitHub. Click **Authenticate**, sign in to GitHub,
   and install/authorise the **Google Cloud Build** GitHub app when asked. Grant
   it access to the `wiseau` repository.
4. **Repository:** select `Thomas-Amann-IPAustralia/wiseau`. Click **Next**.
5. **Branch:** `^main$` (that is a regular expression; leave the `^`/`$`).
6. **Build type:** choose **Dockerfile**.
7. **Source location:** `/backend/Dockerfile`  ← **this is the step people get
   wrong.** The repository root has no Dockerfile; the backend's is in
   `backend/`.
8. Click **Save**.

### 4.5 Service settings

- **Service name:** `wiseau`.
- **Region:** `us-central1` if you want the largest free allowance (Google's free
  network egress is from North America). Pick `australia-southeast1` instead if
  you care more about latency than about a few cents of egress. Either works.
- **Authentication:** select **Allow unauthenticated invocations**. This is
  required — an LLM's connector has no Google credentials, so a service that
  demands them simply cannot be reached. (§8 covers what to do about the fact
  that this makes the URL public.)
- **Ingress control:** **All** (accept traffic from the internet).

### 4.6 Container settings — the ones that matter

Expand **Container(s), volumes, networking, security**, then the **Container**
tab:

| Setting | Value | Why |
| ------- | ----- | --- |
| **Container port** | `8080` (leave the default) | Cloud Run injects `PORT=8080` and the image binds whatever `$PORT` says (ADR-029). Do not change it to 7860. |
| **Memory** | **2 GiB** | Headless Chromium plus a PDF parse will exceed 512 MB and be killed mid-request. |
| **CPU** | 1 | Enough; more only burns free-tier seconds faster. |
| **Request timeout** | **300** seconds | A cold start plus a slow render can take a while. The default is usually already 300. |
| **CPU allocation** | **CPU is only allocated during request processing** | This is what makes idle time free. |
| **Minimum instances** | **0** | Scale to zero. Costs nothing when nobody is using it, at the price of a cold start on the first request. |
| **Maximum instances** | **2** | A hard ceiling on what any runaway traffic can cost you. Raise it later if you ever need to. |

Under the **Variables & Secrets** tab you need **nothing** for a basic
deployment — the defaults are correct. §7 lists the optional ones.

### 4.7 Deploy

1. Click **Create**.
2. The first build takes roughly 5–10 minutes (it is installing Chromium and
   Tesseract into the image). You can watch it under **Cloud Build → History**.
3. When it finishes, the service page shows a URL at the top, of the form
   `https://wiseau-<hash>-uc.a.run.app`. **Copy it.** This is your wiseau.

### 4.8 First check

Open `<your URL>/ping` in a browser. You should see:

```json
{"status":"ok","service":"markdown-ingestion-engine","version":"0.8.0",
 "engines":["docling","pymupdf"],"default_engine":"pymupdf","mcp_endpoint":"/mcp"}
```

`"mcp_endpoint":"/mcp"` is the important part: the container is serving an MCP
connector, and it is telling you where. **Your connector URL is
`<your URL>/mcp`.**

If the first request times out, try again — that was a cold start.

---

## 5. Verify it properly

`/ping` proves the container is alive. These prove the MCP surface works.

**A. From a terminal** — ask the server to introduce itself:

```bash
curl -sS -X POST "https://<your URL>/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"curl","version":"0"}}}'
```

You want a response containing `"serverInfo":{"name":"wiseau"…}`. Anything else
— see §11.

**B. With the MCP Inspector** — a browser tool that speaks MCP properly, which is
the closest thing to seeing what a client sees. Needs Node.js installed locally:

```bash
npx @modelcontextprotocol/inspector
```

It opens a page. Set **Transport Type** to `Streamable HTTP`, **URL** to
`https://<your URL>/mcp`, click **Connect**, then **List Tools**. You should see
`convert_url`, `convert_file`, `ping`. Run `convert_url` with
`https://example.com` and watch real Markdown come back.

**C. The plain REST API**, which is unchanged and still there:

```bash
curl -sS -X POST "https://<your URL>/convert/url" \
     -H 'Content-Type: application/json' \
     -d '{"url":"https://example.com"}'
```

---

## 6. Stop it from ever costing you money

Cloud Run's always-free tier is roughly **2 million requests**, **360,000
GiB-seconds of memory**, and **180,000 vCPU-seconds** per month. At the 2 GiB / 1
vCPU sizing above, and with CPU allocated only during requests, that works out to
about **50 hours of actual request-processing per month** — where a conversion
takes seconds. Personal use will not come close.

Set the guards anyway. Both take a minute:

**Cap the blast radius** (you did this already if you set maximum instances to 2
in §4.6). Cloud Run bills per instance-second, so a ceiling on instances is a
ceiling on spend.

**Get told immediately if anything bills:**

1. Search bar → **Billing** → **Budgets & alerts** (left sidebar).
2. Click **Create budget**.
3. **Name:** `wiseau`. **Scope:** leave it at the whole billing account.
4. **Amount:** target amount `1` (one dollar).
5. **Actions:** leave the default thresholds (50%, 90%, 100% of actual spend) —
   they email you.
6. **Finish**.

You will now hear about a $0.50 charge, long before it becomes a $50 one.

---

## 7. Optional settings

Set these under **Variables & Secrets → Environment variables** on the Cloud Run
service (or as environment variables on any other host). None are required.

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `WISEAU_MCP_PATH` | `/mcp` | Moves the MCP endpoint. Set it to something unguessable — `/mcp-7f3a9c2e1b` — and the connector URL becomes a shared secret. See §8. |
| `WISEAU_MCP_MOUNT` | `1` | `0` turns the MCP endpoint off entirely, leaving only the REST API. |
| `WISEAU_MCP_ALLOWED_HOSTS` | *(unset)* | Comma-separated hostnames the MCP endpoint will accept requests for. Unset means "any", which is what a public deployment needs. Set it to your service's hostname if you want the check. |
| `MAX_CONCURRENT_JOBS` | `4` | How many conversions run at once in one container. Lower it if you see out-of-memory restarts. |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Upload size ceiling. |
| `WISEAU_PDF_ENGINE` | `pymupdf` | `docling` makes the slow, high-fidelity engine the default. Needs `WISEAU_DOCLING_BASE` too, and a second service to run docling — see `docling/README.md`. Leave it alone for now. |
| `WISEAU_ALLOW_PRIVATE_URLS` | *(unset)* | Leave unset. Setting it lets callers make your server fetch private/internal addresses (ADR-021). |

After changing any of these, Cloud Run deploys a new revision automatically —
give it 30 seconds before re-testing.

---

## 8. Be honest about what is and isn't protected

Your URL is public and **unauthenticated**. Anyone who has it can convert
documents using your quota. That is a deliberate trade: no MCP client's connector
dialog lets you attach an API key header, so demanding one would lock out the
very clients this exists for.

What actually protects you, in order of how much it helps:

1. **Nobody knows the URL.** Cloud Run URLs contain a random hash and are not
   listed anywhere. Setting `WISEAU_MCP_PATH` to an unguessable path (§7) turns
   the full connector URL into a shared secret. This is obscurity, not security —
   but it is the only access control that works with every client, and against
   opportunistic scanning it is most of the practical benefit.
2. **The maximum-instance ceiling and the budget alert** (§6). These are what
   actually bound the damage if the URL does leak.
3. **Rate limiting.** wiseau caps conversions per caller (20/minute) and total
   in-flight jobs. Note the wrinkle recorded in ADR-029: MCP tool calls reach the
   conversion routes over the container's own loopback interface, so *all* MCP
   callers share one 20/minute bucket rather than getting one each. Fine for
   personal use; something to revisit if wiseau ever serves many people.
4. **Don't paste the URL into a public place** — a GitHub issue, a screenshot, a
   shared chat.

If you later need real authentication for a genuinely public service, that means
OAuth, which the MCP spec supports and this deployment does not implement. It is
a bigger piece of work, not a setting.

---

## 9. Connect an LLM

You have a connector URL: `https://<your URL>/mcp`. Here is where it goes.

### Claude (claude.ai in a browser, or the desktop app)

1. Click your **name/avatar** (bottom left) → **Settings**.
2. Open **Connectors**.
3. Click **Add custom connector** (it may be behind a **Browse connectors** page,
   at the bottom).
4. **Name:** `wiseau`. **Remote MCP server URL:** `https://<your URL>/mcp`.
5. Click **Add**. Leave the OAuth Client ID/Secret fields empty — this server
   doesn't use them.
6. It should show as connected. In a chat, open the **tools/attachments** menu
   (the sliders or paperclip icon by the message box) and make sure `wiseau` is
   enabled.
7. Try it: *"Use wiseau to convert https://example.com to Markdown."*

*Custom connectors are a paid-plan feature (Pro/Max/Team/Enterprise). If you do
not see "Add custom connector", that is why — use Claude Code below instead,
which is free to configure.*

### Claude Code

One command, no UI:

```bash
claude mcp add --transport http wiseau https://<your URL>/mcp
```

Then `/mcp` inside Claude Code to confirm it connected, and ask it to convert
something.

### ChatGPT

1. **Settings** → **Connectors**.
2. You may need to turn on **Developer mode** (under Settings → Connectors, or
   Settings → Advanced, depending on your plan) to add an arbitrary MCP server.
3. **Create** / **Add custom connector** → paste `https://<your URL>/mcp` →
   choose **No authentication**.

Also a paid-plan feature, and OpenAI has changed the rules here more than once.
If your plan won't take a custom MCP server, the fallback in the next section
works from any GPT with function calling.

### Any other LLM or agent framework

Two routes, both already supported:

- **It speaks MCP:** point it at `https://<your URL>/mcp` with transport type
  `streamable HTTP`. That covers most agent frameworks, IDE agents, and the
  Anthropic and OpenAI APIs' own remote-MCP support.
- **It only does function/tool calling:** skip MCP entirely and hand it
  `https://<your URL>/openapi.json`. That schema describes `convert_url`,
  `convert_file` and `ping` as ordinary HTTP endpoints with clean operation IDs,
  which is exactly the shape a tool-calling loop wants. See
  [`mcp.md` §2](mcp.md).

### A local agent instead

If the agent runs on your own machine and you would rather not deploy at all,
`backend/mcp_server.py` still runs as a local subprocess over stdio — see
[`mcp.md` §1](mcp.md). The hosted route above is what makes wiseau usable by an
LLM that *isn't* on your machine.

---

## 10. Alternative: Render

Simpler UI, meaningfully less memory. Fine for PDF/DOCX uploads; expect URL
rendering to struggle in 512 MB.

1. **<https://dashboard.render.com>** → **New +** → **Web Service**.
2. **Connect a repository** → authorise GitHub → pick `wiseau`.
3. **Language:** `Docker`.
4. **Branch:** `main`.
5. **Root Directory:** `backend`  ← so Render finds `backend/Dockerfile`.
6. **Instance Type:** **Free**.
7. **Create Web Service.**

Render injects its own `PORT`; the image follows it (ADR-029), so there is
nothing to configure. Your URL is `https://<name>.onrender.com` and your
connector URL is `https://<name>.onrender.com/mcp`.

Free services sleep after 15 minutes idle and take ~50 seconds to wake, so the
first tool call after a quiet period may time out in the client. Ask again.

---

## 11. Alternative: Hugging Face Spaces

The most memory for free (16 GB), and the repo is already prepared for it — see
the deployment section of [`backend/README.md`](../backend/README.md), whose YAML
frontmatter *is* the Space configuration. In outline:

1. **<https://huggingface.co/new-space>** → name it, **SDK: Docker**, **Blank**
   template, hardware **CPU basic (free)**.
2. Push the **contents of** `backend/` to the Space repo's root (not the whole
   wiseau repo — the Space expects `Dockerfile` and `README.md` at its root).
3. Wait for the build; the Space URL is `https://<user>-<space>.hf.space`.
4. Connector URL: `https://<user>-<space>.hf.space/mcp`.

Spaces bind port 7860, which is this image's default, so again nothing to
configure.

---

## 12. Wire up the web UI too (optional)

The static frontend is a separate, free deployment on GitHub Pages, and it needs
to know the same URL:

1. In GitHub: **Settings → Pages → Source** → **GitHub Actions**.
2. **Settings → Secrets and variables → Actions → Variables** → **New repository
   variable**: name `MARKDOWN_API_BASE`, value `https://<your URL>` (no trailing
   slash).
3. **Actions → Deploy frontend → Run workflow.**

The workflow injects that value into the published `config.js` (ADR-023), so the
committed default stays `localhost` and your repository never carries your
deployment's URL.

---

## 13. Troubleshooting

| What you see | What it means | Fix |
| ------------ | ------------- | --- |
| Build fails: "Dockerfile not found" | Cloud Build is looking at the repository root. | Source location must be `/backend/Dockerfile` (§4.4). |
| Cloud Run: "Container failed to start and listen on the port" | The container isn't binding `$PORT`. | Container port must be `8080` on Cloud Run — the image reads `$PORT` (§4.6). Check **Logs** on the service page. |
| `/ping` shows `"mcp_endpoint":null` | The MCP endpoint isn't being served. | You set `WISEAU_MCP_MOUNT=0`, or the image was built without `requirements-mcp.txt`. Rebuild from `main`. |
| `421 Misdirected Request` | The Host-header check rejected your hostname. | Unset `WISEAU_MCP_ALLOWED_HOSTS`, or add your service's hostname to it (§7). |
| `307 Temporary Redirect` on `/mcp` | You are hitting `/mcp/` with a trailing slash, or an old build. | Use `/mcp` exactly, and redeploy from `main`. |
| `429 Too Many Requests` | Rate limit. | Wait a minute. Remember all MCP callers share one bucket (§8). |
| `502` on a URL conversion | The render failed — often the page blocked the headless browser, or the container ran out of memory. | Check the service logs. If they show the container being killed, raise memory to 2 GiB (§4.6). |
| First request always times out, then works | Cold start from zero instances. | Normal. Set minimum instances to 1 if it bothers you — that costs money, since the container then runs continuously. |
| Client connects but lists no tools | It connected to the wrong path, or to the REST API root. | The connector URL ends in `/mcp` — check `/ping`'s `mcp_endpoint` for the exact path. |

To read logs: Cloud Run → your service → **Logs** tab. Every request appears as
one structured JSON line with a request id; `GET /metrics` on your service shows
counters for conversions, engines, timings, and memory.
