# M6 Live Acceptance — Take Management

**Date:** 2026-08-17
**Project:** proj_b8b1b8ab40b5
**Shot:** shot60190e252ed4 (废弃地铁站 — abandoned subway station)
**Approved reference:** ref_e11cf946d0cb (CHARACTER_BODY, pinned)

---

## M6.F1 — Three Real Queued Takes

### Enqueue

| Field | Value |
|---|---|
| Batch ID | qb_21e3ff2101dd |
| Idempotency key | m6f-live-shot60190e252ed4-v1 |
| Base seed | 600600 |
| Takes count | 3 |
| Take numbers | 2, 3, 4 (after existing M5 Take 1) |

### Take Results

| Field | Take 2 | Take 3 | Take 4 |
|---|---|---|---|
| Take ID | take4c83761758a1 | *(succeeded)* | takebf852ec8f1a9 |
| QueueJob ID | qj_9b85fecaff4b | qj_b3d3713bd01f | qj_c8d76de9d1b7 |
| Seed | 5592037981451149476 | 8601004437499471111 | 1210841307583178909 |
| ComfyUI prompt | 9e950d62-... | 1a23dc30-... | 3645ff00-... |
| Resolution | 1376x768 | 1376x768 | 1376x768 |
| Duration | 5.167s | 5.167s | 5.167s |
| Codecs | H.264 + AAC | H.264 + AAC | H.264 + AAC |
| Video SHA | 494cd121... | b87a43be... | 32c93d99... |
| Recovered | No | No | **Yes** |
| Human visual | PASS | PASS | PASS |
| Final status | **APPROVED** | succeeded | succeeded |

### Human Visual Verdict: PASS

Character identity, appearance, motion, and general video result acceptable for all three Takes. Take 2 approved as deterministic first-acceptable fallback.

### Chinese Audio Observation

All three Takes contain Chinese-language speech. Root-cause classification:

**A + C**: The H3 prompt contains Chinese text from WC source data (camera angle: `大远景, 仰拍, 缓慢横移跟拍, 伦勃朗光`; lighting: `伦勃朗光, 右上45°暖黄主光, 环境冷蓝阴影像素, 背景低调暗影`; character name: `陆砚`). No explicit dialogue or language instruction was present (audio_intent is empty). H3 native audio likely inferred Chinese speech from the Chinese-language prompt context.

**Deferred to M10 audio handling:**
- Explicit output-language control
- Validation that H3 speech matches requested/project language
- Ability to disable or replace unsuitable native audio

### Worker Restart Recovery Evidence

Take 4 experienced a WebSocket keepalive timeout during generation. ComfyUI completed the prompt (3645ff00-...) successfully. The worker was terminated, job reset to `claimed`, and a new worker instance recovered:

1. `check_prompt_status(3645ff00-...)` → `"succeeded"`
2. `finalize_from_result()` — downloaded/validated/finalized existing output
3. `submit()` was NOT called — zero duplicate workflows
4. Take was created with exact persisted seed (1210841307583178909) and take_number (4)
5. Recovery runtime: ~66s (download+validate only, no re-generation)

---

## M6.F2 — 20-Shot / 60-Job Queue Proof

| Field | Value |
|---|---|
| Database | data/m6_queue20.db (separate) |
| Shots | 20 (ordered) |
| Takes per shot | 3 |
| Total jobs | 60 |
| Idempotency key | m6f-queue20-proof |
| Base seed | 999999 |
| All statuses | pending |
| ComfyUI submissions | 0 |

**Proof:**
- All 60 jobs persisted with unique IDs and deterministic seeds
- Idempotent replay returns same batch and 60 job IDs
- After database close + reopen: all 60 jobs preserved and identical
- After-reopen replay: same batch and 60 job IDs
- Zero ComfyUI prompts submitted

---

## M6.F3 — Approval

| Check | Result |
|---|---|
| Take 2 approved | status=approved |
| Idempotent re-approve | returns same Take |
| GET approved-take | returns Take 2 |
| Approved count | exactly 1 |
| Takes 3, 4 | succeeded, not favorite |
| No Takes rejected | correct |
| Files unchanged | SHA verified |
| After DB reopen | approved Take 2 persists |

---

## M5 Source Preservation

| File | Pre-copy SHA | Post-M6 SHA | Match |
|---|---|---|---|
| M5 production.db | e6aa9a50... | e6aa9a50... | YES |
| M5 ref image | 734dff27... | 734dff27... | YES |
| M5 video | f6119ceb... | f6119ceb... | YES |

M5 worktree, database, and media were never modified during M6.F.

---

## Database Integrity

| Database | integrity_check | foreign_key_check |
|---|---|---|
| data/m6_live.db | ok | 0 violations |
| data/m6_queue20.db | ok | 0 violations |
