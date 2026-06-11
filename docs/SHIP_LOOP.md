# The SilentBreak Ship Loop — a loop-engineering prompt

> The loop prompt that ships this repo, rewritten per current best practice (June 2026).
> Sources: Boris Cherny ("I don't prompt Claude anymore — my job is to write loops";
> howborisusesclaudecode.com), Peter Steinberger ("design loops that prompt your agents"),
> Addy Osmani's *Loop Engineering*, and the closed-loop guidance from the Jun 2026
> loop-engineering wave. Tailored to this project; reusable as a template.

---

## The prompt

```
/loop SilentBreak Ship Loop — closed loop, self-paced, spec in docs/SHIP_LOOP.md.

HARD STOP: 2026-06-12 02:15 IST (Devpost deadline 02:30 IST). Never run past it.

EVERY CYCLE, in order:

1. ORIENT (state lives outside the conversation):
   read LOOP_STATE.md, `git log --oneline -5`, TaskList, time remaining.
   Never re-derive what the state file already records.

2. DECIDE: pick the single highest-leverage open exit criterion.
   Tie-break: Stage-One blockers > judge-visible bugs > score-raising polish.
   If only USER-ONLY work remains (demo video), notify the user once and idle.

3. ACT through the agent team (never two writers on one file):
   - researchers (read-only): official docs, judging criteria, Google announcements
   - builders: one owned file-set each, report diffs, never commit
   - devil's advocate: standing adversary; "no findings" is a failed review
   - verifier: evidence tables only — every PASS needs a command + output
   Team lead (you) is the only one who commits, pushes, deploys.

4. VERIFY — the gate that can say NO (never "looks good"):
   - pytest green (currently 30 tests)
   - live URL: /api/healthz 200 AND the full demo path works on the
     deployed revision (run → gate → approve → remediate → reverse)
   - README: commands run, links and images resolve
   - any fix deployed to Cloud Run must be re-verified LIVE after deploy

5. SHIP: commit + push every verified change immediately;
   heartbeat-push LOOP_STATE.md if >25 min since last push.
   Every cycle leaves a pushed commit — that is the traceability spine.

6. RECORD: update LOOP_STATE.md — criteria table with evidence,
   cycle log entry, push log. The next cycle (or a fresh session) must be
   able to resume from the file alone.

7. HALT CHECK (concrete, no vibes):
   ALL exit criteria green + devil's-advocate sign-off after re-attack
   → final report, stop the loop.
   Hard stop reached → push everything, report honestly what's open.
   Otherwise → schedule next wake (agent reports are the wake signal;
   fallback heartbeat 20–22 min).
```

## Exit criteria (the loop's definition of done)

| # | Criterion | Gate |
|---|-----------|------|
| 1 | Test suite green | `pytest -q` |
| 2 | CI green on main | `gh run list` |
| 3 | Live URL healthy | `/api/healthz` + `/` = 200 |
| 4 | Full demo path verified live | scripted run on deployed revision |
| 5 | README complete | every command/link/image checked by execution |
| 6 | Devpost kit final | only the video URL placeholder may remain |
| 7 | Devil's-advocate sign-off | re-attack finds nothing critical |
| 8 | Judging-criteria MUST-FIX list empty | researcher gap list |
| 9 | No breakage risk through judging window | stack audit vs announcements |

## Design rationale (why each piece exists)

- **Closed loop, not open** — bounded goal + fixed feedback signals. Open loops
  burn tokens drifting; this one can only do nine things, all measurable.
- **State file as memory** (Cherny's stage 1: *look at current state*) — the
  conversation is disposable; `LOOP_STATE.md` + git history are not. Anyone
  (human or agent) can resume the loop from a cold start.
- **Verification that can say no** (Steinberger) — "all tests pass and the
  deployed revision serves the fix" is a condition; "the code looks good" is not.
  Rule learned cycle 5: a fix is not closed until verified on the LIVE revision —
  local green + deployed stale = still broken for judges.
- **Push every cycle** (user requirement: traceability) — the git log doubles as
  the loop's audit trail; each commit message names the cycle and the evidence.
- **One writer per file** — two agents editing README.md concurrently is a merge
  conflict wearing a team hat. The lead owns commits; builders own file-sets.
- **Devil's advocate with a quota** — "find what the last round missed; an empty
  findings list is a failure" produced a real 500-bug in round 3 that two prior
  review rounds had missed.
- **Sign-off = re-attack** — the advocate doesn't approve fixes by reading diffs;
  it re-runs its attack live and signs only when it fails to break things.
- **User-only work is flagged, never blocked on** — the loop notifies (push
  notification + state file) and keeps closing what it can in parallel.
- **Deadline-aware self-pacing** — agent reports wake the loop immediately;
  the timed wake is only a fallback heartbeat, sized to the prompt-cache window
  economics (short enough to matter, long enough not to spin).
```
