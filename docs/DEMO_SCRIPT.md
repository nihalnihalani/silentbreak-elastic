# SilentBreak: 3-minute demo recording plan

Target: 2:50 to 3:00 final cut. One continuous screen take if possible; the loop itself
runs in about 60 seconds, leaving room for setup and the reverse.

## Pre-recording checklist

- [ ] Window at 1280x800, browser at 100% zoom, no bookmarks bar, no other tabs.
- [ ] Stack fresh: `make up && make seed && make inject` (inject again if you did a dry run;
      a remediated run consumes the poison).
- [ ] Server: `SILENTBREAK_MODE=real make web` (badge must read DETERMINISTIC FALLBACK
      unless you have a GOOGLE_API_KEY in `.env`, in which case it reads ADK).
- [ ] Second terminal visible for the cold open: `curl -s localhost:9200/_cat/aliases` and
      `python scripts/run_demo.py` ready in history.
- [ ] Do one full rehearsal, then `make inject`, then record.
- [ ] Mic check; speak about 20% slower than feels natural.

## Beats

### 0:00 to 0:20 | The lie (cold open on the UI, idle)

On screen: the Polygraph at idle. Cursor circles the green tile.

Say: "This pipeline just reported run 4471: SUCCESS. Every monitor is green. It is also
shipping a revenue table where one hundred percent of the amounts are null, because an
upstream SDK migration renamed the column. Dashboards cannot see this. SilentBreak is a
polygraph for exactly this lie: pipelines whose status says SUCCESS while their data says
corrupt."

### 0:20 to 0:35 | The stack, in one breath

On screen: stay on the UI; point at the MODE/ENGINE badges.

Say: "It is a Google ADK agent pipeline, Gemini-driven, and every read goes through the
official Elastic MCP server into Elasticsearch, where the data, the detector, and the
actuator all live. Watch it work."

### 0:35 to 1:10 | The examination (click RUN EXAMINATION)

On screen: needles draw left to right across the chart paper as the Sentinel sweeps nine
healthy days into today. The breach spikes, red z=40 line inks in, the CONTRADICTION
stamp slams over the green tile. The teletype prints the raw ES|QL.

Say: "The Sentinel fingerprints each partition through MCP: null rate, average amount,
distinct SKUs, z-scored against a rolling baseline. History is calm. Today the needle
goes off the paper: null rate from point two percent to one hundred percent, z of forty,
while the status doc still says SUCCESS. That contradiction is the alarm."

### 1:10 to 1:35 | The diagnosis (typewriter)

On screen: the examiner's report types itself: the sentence, then `- amount` and
`+ gross_amount`, then the estimated exposure.

Say: "The root cause agent diffs today's index mapping against yesterday's. Minus amount,
plus gross_amount. The loader kept reading amount, so ten thousand rows carry no revenue:
about six hundred seventy six thousand dollars invisible downstream, and it labels that
number an estimate."

### 1:35 to 2:10 | The stamp (the centerpiece, slow down here)

On screen: the operator gate shows the four-step plan. Press and hold the APPROVE stamp;
let the ring fill all the way; release; the QUARANTINE APPROVED impression slams down.

Say: "Here is the line the agent will not cross alone. It proposes: quarantine the corrupt
rows, flip the alias to the last known good index, verify, and stay reversible. Nothing
mutates Elasticsearch until I hold this stamp. The hold mints a single-use token; the
Guardian consumes it before any write. Rejecting means zero writes. I approve."

(If you have ten spare seconds, mention: "the stamp is a real cryptographic gate, not a
confirm dialog.")

### 2:10 to 2:35 | The heal (teletype)

On screen: guardian actions print; the verify line shows the downstream query healthy.
Point at the teletype: rows=10000, null_rate=0.002, OK.

Say: "Ten thousand corrupt rows reindexed into quarantine, and one atomic update_aliases
flips revenue_current to yesterday. The downstream revenue query, live on the teletype,
is healthy again. Every consumer healed at once, no code changes anywhere."

### 2:35 to 2:55 | The reverse (the proof)

On screen: hold the REVERSE lever; the teletype prints the alias flipping back; the
header annotates "alias restored, reversibility proven".

Say: "And because honest demos prove their actions are real: one hold reverses it. The
alias flips back. This is a state change, not a video effect. SilentBreak: when the
pipeline swears it succeeded, cross-examine the data, and act only with a human's stamp."

### 2:55 to 3:00 | Card

On screen: title card or README hero: repo URL + hosted URL.

## Fallback take (no docker on the recording machine)

The identical loop runs in mock mode: `make web` and press RUN EXAMINATION. The UI labels
it MOCK (IN-MEMORY ES); say so in the voiceover; the approval gate and alias flip logic
are the same code paths.

## Don'ts

- Do not skip the reject path mention; oversight is a judging criterion.
- Do not call the dollar figure exact; it is computed and labeled as an estimate.
- Do not claim Gemini wrote the report if the badge says DETERMINISTIC FALLBACK.
