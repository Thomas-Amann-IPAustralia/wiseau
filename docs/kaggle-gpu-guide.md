# Kaggle GPU test of OvisOCR3: step-by-step setup guide

This guide gets the prepared GPU test (`benchmarks/bakeoff/kaggle/`) running
on a free Kaggle T4, then brings the results back into the repo to be scored.
It is a **test only**. Nothing here changes Wiseau's deployed behaviour, and an
OvisOCR3 opt-in is decided after the results are in.

**What the test answers:** the CPU test already showed that OvisOCR3 labels
headings and captions a little better than the shipped detector, but at about
2½ minutes a page. This test asks whether, on a T4 GPU, it:
- loads at all;
- runs correctly in fp16 (the T4 has no native bf16);
- runs in a few seconds a page, and at what VRAM;
- gives the same regions as the CPU fp32 reference.

**What it costs:** nothing. It uses Kaggle's free GPU quota (about 30 GPU-hours a
week; this run takes roughly one hour or less) and no paid API.

**What gets uploaded:** a private Kaggle dataset holding two harness scripts and
`pdf-test/` (public PBR journals and patent specifications), plus a private
notebook that runs them. Nothing is public unless you make it so.

---

## Part 1: one-time Kaggle setup (about 10 minutes)

1. **Create a Kaggle account** at <https://www.kaggle.com> (or sign in).
2. **Verify your phone number.** Open your profile → **Settings**, find
   **Phone verification**, and follow the prompts. Kaggle has required this before
   a notebook may use a GPU or the internet. If your account is already
   verified, or Kaggle no longer asks, skip this step. The test needs both
   (internet for `pip install` and the model download).
3. **Create an API token.** In **Settings → API**, choose one:
   - **Create New Token** (the current kind): Kaggle shows a token string.
     Copy it.
   - **Create Legacy API Key**: Kaggle downloads `kaggle.json`, holding your
     username and a key.

   Treat either as a password. Never paste it into a chat, an issue or a commit.
4. **Note your Kaggle username** (shown on your profile URL,
   `kaggle.com/<username>`). The scripts use it to name the dataset and
   notebook.

## Part 2: choose where to run it

Pick **one**.

- **Path A: from your own computer (recommended).** The token never leaves your
  machine, and the steps are plain shell commands.
- **Path B: from a Claude cloud session.** You add the credential once as a
  network secret, and Claude runs, watches and scores everything.

---

## Path A: run it from your own computer

**Requirements:** Python 3.11+, git, and about 100 MB of disk.

1. **Install the Kaggle CLI.**
   ```bash
   pipx install kaggle        # or: pip install kaggle   (or: uv tool install kaggle)
   kaggle --version
   ```
2. **Give the CLI your token.** Use the one matching what you created in
   Part 1, step 3:
   ```bash
   # New token:
   export KAGGLE_API_TOKEN=<paste the token here, in your terminal only>
   # or save it once:  mkdir -p ~/.kaggle && echo -n '<token>' > ~/.kaggle/access_token && chmod 600 ~/.kaggle/access_token

   # Legacy key:
   mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/ && chmod 600 ~/.kaggle/kaggle.json
   ```
   Check it works: `kaggle kernels list --mine` should print a (possibly empty)
   list, not an authentication error.
3. **Get the repo at the branch that holds the test.**
   ```bash
   git clone https://github.com/Thomas-Amann-IPAustralia/wiseau.git
   cd wiseau
   git checkout ccr-d5bdc0ba-1jgy26      # or main, once merged
   ```
4. **Launch the test.**
   ```bash
   KAGGLE_USERNAME=<your-username> benchmarks/bakeoff/kaggle/push.sh
   # optional, also try the vLLM route:
   # RUN_VLLM=1 KAGGLE_USERNAME=<your-username> benchmarks/bakeoff/kaggle/push.sh
   ```
   This does two things:
   - creates the private dataset `<username>/wiseau-layout-spike` (about 50 MB
     upload). The first time takes a minute while Kaggle processes it.
   - pushes and starts the private notebook `<username>/wiseau-layout-spike-run`
     on a GPU T4 machine, with internet on.

   If the dataset is still processing, the push can fail with "dataset not
   found". Wait a minute and run the same command again; it updates rather than
   duplicates.
5. **Wait for it to finish** (expect 30–60 minutes).
   ```bash
   kaggle kernels status <your-username>/wiseau-layout-spike-run
   ```
   You can also watch it live: **kaggle.com → Your Work → Code →
   wiseau-layout-spike-run → Logs**.
6. **Download the results.**
   ```bash
   KAGGLE_USERNAME=<your-username> benchmarks/bakeoff/kaggle/push.sh fetch
   cd benchmarks/bakeoff/outputs/kaggle-$(date +%Y%m%d) && unzip -o outputs.zip && cd -
   ```
7. **Commit the outputs straight away.** They are evidence, and a lost run
   costs another hour of quota.
   ```bash
   git add benchmarks/bakeoff/outputs/kaggle-*
   git commit -m "Kaggle T4 run: OvisOCR3 layout regions (raw outputs)"
   git push
   ```
8. **Ask Claude to score them** ("score the Kaggle run"). Or do it yourself;
   see Part 4.

---

## Path B: let a Claude cloud session run it

1. **Start a Claude Code session on this repo** (claude.ai/code), on the branch
   above.
2. **Tell Claude:** "Run the Kaggle GPU leg. My Kaggle username is
   `<username>`." (A username is not a secret.)
3. **Claude asks for the credential through a secure form.** It never appears in
   the chat. The form saves it as a *network secret* on the session's
   environment, and the environment's proxy adds it to requests to
   `www.kaggle.com`. Choose:
   - **Legacy API key** (recommended for this path): type **basic** (username
     and password), username = your Kaggle username, password = the `key` value
     from `kaggle.json`. The Kaggle CLI's legacy authentication is HTTP basic
     auth, so the proxy can supply it.
   - A new-style token works only if Kaggle accepts it as an `Authorization:
     Bearer` header. Claude checks with one small request and tells you if not.

   The secret is stored on the environment and used by later sessions in it.
   Remove it in the environment's settings when you no longer want that.
4. Claude then:
   - installs the CLI with a placeholder credential, so the real one only ever
     comes from the proxy;
   - checks access with `kaggle kernels list --mine`;
   - runs `push.sh`;
   - checks the status about every 10 minutes;
   - fetches and commits the outputs;
   - scores them and reports.

**What can block Path B:** if the session's network policy doesn't allow
`www.kaggle.com`, Claude proposes the change and you approve it. Saving the
credential already allows that host.

---

## Part 3: what the notebook runs (`benchmarks/bakeoff/kaggle/kernel.py`)

Every step's command, return code and output tail go to
`outputs/kernel-log.jsonl`. One failing step never hides the others.

1. **Environment:** `nvidia-smi`, Python and package versions, before any
   install.
2. **Transformers route.** Kaggle's own torch is left alone. Only
   `transformers==5.22.1` and `accelerate==1.15.0` are installed, as on the CPU
   test.
   - **fp16, one page** (V2 p9), then **bf16** and **fp32** on the same page
     as diagnostics;
   - **fp16 on set S (20 pages) and set B (10 pages):** the comparison against
     the CPU reference;
   - the **structured** prompt (regions with text) on 3 pages, for
     inspection only. Its text is never used.
   - PP-DocLayoutV3 on set S, to put the GPU in scale.
3. **Optional vLLM route** (`RUN_VLLM=1`): the model card's vLLM 0.22.1, in its
   own virtualenv, on one page. A refusal is a result: vLLM may not support the
   T4's compute capability (7.5).

One GPU, batch 1, one page at a time; the "T4 ×2" machine's second GPU is
not used.

## Part 4: scoring the results (Claude does this, or you can)

```bash
cd benchmarks/bakeoff
cp -r outputs/kaggle-<date>/fp16/ovisocr3-layout outputs/ovisocr3-layout-t4fp16
python layout_regions.py build ovisocr3-layout-t4fp16 --base tables-tatr --v2
python layout_regions.py score tables-tatr pp-doclayoutv3-regroup-tatr-v2 ovisocr3-layout-regroup-tatr-v2 \
    ovisocr3-layout-t4fp16-regroup-tatr-v2 --gold s
```

Set T (the held-out pages) is not in the Kaggle run on purpose. Once the fp16
regions match the CPU reference on sets S and B, set T's CPU numbers stand for
the GPU too.

Then read three things:
- **Speed and memory:** `seconds` and `peak_vram_mb` in each
  `*.regions.json`.
- **Agreement with CPU:** same categories and boxes within a few points on most
  pages. The set S scores should be close to the CPU row.
- **Failures:** fp16 NaNs, empty output or `parse_error` entries, and the
  vLLM step's result.

## Part 5: what the results decide

OvisOCR3 earns an **opt-in** (`engine`-style choice, never the default) only if
all of these hold on the T4:
- fp16 works, with no NaNs or empty pages;
- every page gives valid JSON;
- it is fast enough to be worth offering (aim for well under 10 s a page);
- its set S scores match the CPU reference.

If any fails, the shipped CPU detector stays the only layout source and the
experiment is closed with the evidence on record.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` / "Could not find kaggle.json" | Token not set, or the wrong kind for how it's provided | Repeat Part 1 step 3 and Path A step 2; `kaggle kernels list --mine` must work first |
| Push says the dataset is not found | Kaggle is still processing the new dataset | Wait a minute, run `push.sh` again |
| Push rejects `machine_shape` | An older CLI version | `pip install -U kaggle`. Or delete the field from `push.sh` and pick **GPU T4 ×2** under the notebook's **Settings → Accelerator** on the website, then **Save Version** |
| Notebook runs on CPU / "GPU not available" | Phone not verified, or the weekly GPU quota used up | Part 1 step 2; quota shows at **kaggle.com → Settings / Your Work**, resets weekly |
| `pip install` fails inside the notebook | Internet off for the notebook | Phone verification; confirm **Internet: On** in the notebook's settings |
| fp16 step has NaNs or empty output but fp32 works | fp16 overflow in the linear-attention layers | A result in itself: record it. It means the T4 needs fp32 (slow) |
| vLLM step fails | vLLM doesn't support this GPU or model on CC 7.5 | Expected possibility: record it, don't patch around it |
| Dataset title/license rejected | Kaggle's metadata rules (title 6–50 chars, one licence) | Edit `dataset-metadata.json` in `push.sh` (e.g. licence `CC0-1.0`) |

The scripts are `benchmarks/bakeoff/kaggle/{push.sh,kernel.py,vllm_smoke.py,README.md}`.
The background is in `docs/benchmarks/2026-11-10-layout-regions-set-t.md`.
