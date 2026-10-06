# Demo video — dashboard walkthrough

**Artifact:** [`demo-dashboard-walkthrough.webm`](demo-dashboard-walkthrough.webm) — 1280×900, ~55 s, 1.3 MB, VP8/WebM (captured from the live operator dashboard on `:8100`).

WebM plays natively in Chrome, Edge, Firefox, Slack, and LinkedIn's uploader.
`ffmpeg` is not installed on this machine, so an `.mp4` transcode is a one-liner
wherever it is:

```bash
ffmpeg -i marketing/demo-dashboard-walkthrough.webm \
       -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart \
       marketing/demo-dashboard-walkthrough.mp4
```

The recording is **of real state, not a mock-up**: a real ticket was pushed
through the pipeline and a real review was approved on camera, so the cost
figures and latency in the video are the ones the code produced.

---

## Shot list (as recorded)

| # | Beat | On screen | Point it makes |
|---|---|---|---|
| 1 | Hold on the header + KPI strip | 8 tickets processed · 75% containment · 1 pending · 25% escalation · p95 69.8 ms · $0.0015 LLM spend · $6,494.5/mo est. savings · component health row all `healthy` | "Is it actually working?" is answered before you scroll |
| 2 | Scroll to Side A / Side B | Process analyzer form (6 steps with minutes/repetitive/judgment/money/PII) + ticket form | Same system answers *should we automate this* and *run it safely* |
| 3 | Click **Run canonical demo ticket** | Live trace table: `intake 3.5ms → knowledge 0.7ms (2 chunks, top=Refund Policy — Duplicate charges) → decision 0.16ms → draft 2.6ms → quality 1.9ms → actions 69.9ms`; disposition `auto_resolved`; `cost $0.00025 · 78.697 ms · risk 0.15` | The pipeline is inspectable per agent, not a black box |
| 4 | Approval queue | `pending rev_bd27646a2f` for `tkt_d959706ad2` — `risk 0.65 · 2FA removal is a security-controlled action`; proposed actions `send_reset_guidance` | The gate that fired is named, and the queue shows *why* |
| 5 | Click **Approve & execute** | Review flips to decided; audit gains a `review_decided` event | Human-in-the-loop is real, and it's audited |
| 6 | Hold on **Recent audit events** | `review_decided`, `actions_executed`, `refund_queued`, `auto_resolved`, `escalated_to_human` as key/value lines that **wrap instead of overflowing** | The panel that used to clip long JSON now shows readable, colour-coded evidence |

## Narration script (for a captioned or voice-over cut)

> **[0:00]** "Every AI support agent gets sold on resolution rate. This one gets
> sold on what it refused to do."
>
> **[0:05]** "One system answers three questions. First: should we automate
> this at all? The analyzer maps each step to automate, assist, human approval
> or keep — and the ROI engine prices it."
>
> **[0:12]** "Second: can we run it safely? Here's a live ticket. Six agents:
> intake, retrieval, a deterministic risk gate, a draft, a quality gate, and
> the actions. Watch the trace — retrieval grounded on the duplicate-charges
> policy, the gate scored it 0.15, and it resolved in 78 milliseconds for
> $0.00025."
>
> **[0:22]** "Now the interesting one. A customer asked us to remove 2FA. Same
> pipeline, different gate: the security policy fires, risk 0.65, and it lands
> in the human queue instead of acting. Note that the gate names itself — '2FA
> removal is a security-controlled action'. Approving it here executes and
> writes the decision to the audit trail."
>
> **[0:38]** "Third: is it actually working? Cost per decision, p50 and p95
> latency, containment, the approval-queue SLA, quality-gate precision and a
> recall proxy that admits what it can't see."
>
> **[0:48]** "And that audit feed on the right is the point of the whole thing —
> every decision, action and dollar, in a format a human can actually read.
> 165 tests. No API keys. Clone it and check my work."

## How to re-record this

```bash
# 1. serve the dashboard with data in it
python -m scripts.demo                    # writes ./data (runs, audit, reviews, usage)
python -m uvicorn backend.main:app --port 8100

# 2. open http://localhost:8100, set the window to 1280x900, start a screen capture
# 3. follow the shot list above; the only two clicks are
#    "Run canonical demo ticket" and "Approve & execute"
# 4. if the approval queue is empty, run scripts.demo again — it seeds a pending
#    high-risk review (rev_74a4702d0b / the 2FA case) for exactly this purpose
```

**Notes on capture:** record the *browser tab*, not the whole desktop, so
notifications and window chrome stay out of frame. Keep the theme on `auto`
so the recording matches whatever the viewer's own dashboard will look like.
Nothing in the video depends on API keys, so the same script works on a
colleague's machine after a `git clone`.

## Per-platform versions

| Platform | Cut | Notes |
|---|---|---|
| LinkedIn | 55 s, as recorded | Native upload; add burned-in captions — most feed views are muted |
| README | animated GIF ≤ 5 MB, or a poster frame that links the WebM | Keep the repo light |
| Portfolio site | 30 s cut: beats 3 → 5 → 6 only | Lead with the trace and the audit feed |
| Conference / demo day | 90 s + the narration script above | Add a beat showing the ROI panel result, which the 55 s cut skips |
