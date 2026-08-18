# P2 Scene Preflight — Real Multi-Shot Scene Validation

**Status:** READY FOR SHOT 1 GENERATION  
**Branch:** `p2-real-scene`

---

## 1. Scene Concept

A cinematic nighttime apartment interior. One recurring adult male character enters a modest apartment carrying a small sealed red envelope. The apartment is dimly lit by a warm practical lamp and cool city light through a window.

The scene tests: character identity persistence, environment consistency, prop continuity (red envelope state changes), spatial continuity, and temporal shot-to-shot handoff.

---

## 2. Wind Comic Integration

**Method:** `POST /projects/from-idea` via existing PreproductionService

**Idea text:**
> A cinematic nighttime scene. A man in a dark coat enters a dimly-lit modest apartment carrying a sealed red envelope. He walks to a small table by the window, sits down, opens the envelope, removes a photograph, and reacts subtly. Then a knock at the door makes him turn. No dialogue. One character. Warm lamp + cool city light through window. 5 shots.

**Prerequisites:**
- Wind Comic: running on port 3000 ✓
- Ollama: **NOT RUNNING** — must be started before execution
- LLM model: qwen3:14b or gemma4:e4b

**Expected WC output:**
- 1 scene
- 1 character
- 5-6 storyboard shots
- Script/director treatment data

**WC project ID:** `jaElem8f9MRrs94gFRrPU`  
**Canonical project ID:** `proj_5339656ad20f`

**Actual WC output:**
- 2 scenes (主场景, 关键场所) — WC created 2 instead of requested 1
- 2 characters (主角/protagonist, 伙伴/companion) — WC added an extra character
- 4 storyboard shots (sparse: duration=10s each, no action/camera/lighting detail)
- Character appearances: empty (WC Writer quality limitation with local LLM)

**Import result:**
- Project: `proj_5339656ad20f`
- 1 sequence, 2 scenes, 2 characters imported
- 0 beats, 0 shots, 0 plans (enrichment not yet run — requires Ollama)

---

## 3. Target Canonical Hierarchy

```
ProductionProject (from WC)
  └── Sequence
        └── Scene (nighttime apartment)
              └── Beat 1: Entry
              │     └── Shot 1: Medium-wide entry
              └── Beat 2: Cross to table
              │     └── Shot 2: Medium tracking
              └── Beat 3: Open envelope
              │     └── Shot 3: Medium-close at table
              └── Beat 4: React to contents
              │     └── Shot 4: Close-up reaction
              └── Beat 5: Knock at door
                    └── Shot 5: Medium turn
```

Exact hierarchy depends on WC output + LLM enrichment.

---

## 4. Shot Table (Target)

| Shot | Framing | Action | Duration | Continuity | Prop State |
|------|---------|--------|----------|------------|------------|
| 1 | Medium-wide | Enter apartment, close door | 5s | Scene head (no predecessor) | Sealed envelope in hand |
| 2 | Medium | Cross apartment toward table | 5s | Predecessor: Shot 1 last frame | Envelope visible |
| 3 | Medium-close | Sit at table, open envelope | 5s | Predecessor: Shot 2 last frame | Envelope being opened |
| 4 | Close-up | Study contents, subtle reaction | 5s | Predecessor: Shot 3 last frame | Photograph/paper visible |
| 5 | Medium | Knock heard, turn toward door | 5s | Predecessor: Shot 4 last frame | Contents in hand |

---

## 5. Reference Strategy

### A. CHARACTER (Required)

**Need:** One strong identity anchor for the recurring male character.

**Plan:** Generate via existing ReferenceGenerationService using Z-Image Turbo or Krea 2 Turbo (proven M5 workflows). Description: "A man in his 30s-40s wearing a dark coat, European features, neutral expression."

**Status:** Generate after WC import provides character description.

### B. ENVIRONMENT (Required)

**Need:** Apartment interior reference — warm lamp + window with city light.

**Options:**
1. Generate a reference image via Z-Image/Krea2 with environment prompt
2. Use an existing stock/source apartment image ingested via ReferenceIngestService

**Plan:** Generate via existing reference workflows. Or use WC storyboard image if WC returns one.

### C. PROP — Red Envelope (Optional)

**Need:** Red envelope reference for Picture 4 slot consistency.

**Plan:** Generate if needed, or omit for initial validation. The 3 required references (character + environment + continuity) are the critical test.

### D. CONTINUITY FRAME (Auto)

Shot 2-5: Approved predecessor Take's `last_frame.png` automatically becomes Picture 3.

---

## 6. Workflow Selection Per Shot

| Shot | Workflow | Pictures Used |
|------|----------|---------------|
| 1 | h3_r2v_v2 (2-ref) | Pic 1: character, Pic 2: environment |
| 2 | h3_r2v_image_pack_v1 (3-4 ref) | Pic 1: character, Pic 2: environment, Pic 3: Shot 1 last frame |
| 3 | h3_r2v_image_pack_v1 | Pic 1: character, Pic 2: environment, Pic 3: Shot 2 last frame |
| 4 | h3_r2v_image_pack_v1 | Pic 1: character, Pic 2: environment, Pic 3: Shot 3 last frame |
| 5 | h3_r2v_image_pack_v1 | Pic 1: character, Pic 2: environment, Pic 3: Shot 4 last frame |

Shot 1 uses r2v_v2 because there is no predecessor frame (scene head).

---

## 7. Sequential Approval Plan

```
Generate Shot 1 → Review → APPROVE one Take
↓
Generate Shot 2 (using Shot 1 approved last_frame) → Review → APPROVE
↓
Generate Shot 3 (using Shot 2 approved last_frame) → Review → APPROVE
↓
Generate Shot 4 (using Shot 3 approved last_frame) → Review → APPROVE
↓
Generate Shot 5 (using Shot 4 approved last_frame) → Review → APPROVE
↓
SCENE COMPLETE
```

No batch generation. Each shot waits for predecessor approval.

---

## 8. Runtime Prerequisites

| Prerequisite | Status | Action |
|---|---|---|
| Wind Comic (port 3000) | ✓ Running | — |
| ComfyUI (port 8188) | ✓ Running | — |
| Ollama | **✗ NOT RUNNING** | **Start before execution** |
| `.env` file | ✗ Missing | Create with WC credentials |
| LLM model | qwen3:14b or gemma4:e4b | Verify after Ollama start |

---

## 9. Detected Gaps

| Gap | Severity | Existing Workaround |
|---|---|---|
| Ollama not running | **BLOCKER** | Start Ollama manually |
| `.env` file | ✓ RESOLVED | Created with WC credentials |
| WC created 2 chars instead of 1 | PRODUCT_GAP | Use only 主角 (protagonist), ignore 伙伴 |
| WC created 2 scenes instead of 1 | PRODUCT_GAP | Focus on 主场景, or let enrichment merge |
| WC storyboard shots lack detail | PRODUCT_GAP | Enrichment (Ollama) fills in action/camera/lighting |
| Character appearances empty | PRODUCT_GAP | Define manually or generate via enrichment |
| Shot 1 needs r2v_v2 not image-pack | PRODUCT_GAP | Use existing r2v_v2 for scene heads |
| No environment reference generation prompt | NICE_TO_HAVE | Use character ref workflow with env prompt |
| No automated scene assembly/export | NICE_TO_HAVE | Manual file ordering (P4 scope) |

---

## 10. Required Custom Code

**None for this validation.** All required capabilities exist:
- `POST /projects/from-idea` — WC → import → enrich
- Reference generation via existing ReferenceGenerationService
- H3 generation via GenerationService.generate_take (r2v_v2 for head)
- H3 generation via GenerationService.generate_with_image_pack (shots 2-5)
- Take approval via TakeService
- Continuity via ContinuityService

---

## 11. Generation Readiness

**READY** — all blockers resolved.

**Completed:**
- ✓ Wind Comic preproduction (`jaElem8f9MRrs94gFRrPU`)
- ✓ Canonical import (`proj_5339656ad20f`)
- ✓ Enrichment attempted — LLM (qwen3:14b) hallucinated wrong content (known M4 quality gap)
- ✓ Manual shot correction: 5 human-designed shots matching apartment/envelope scene
- ✓ Character reference generated (Z-Image Turbo, 1024x1024, approved+current)
- ✓ Environment reference generated (Z-Image Turbo, 1024x1024, approved+current)
- ✓ All deterministic tests pass (1716/0 failed)

**References:**
- Character: `ref_5222d638c7f1` — European man, dark coat, approved
- Environment: `ref_512ad37aa29a` — apartment interior, night, approved

**Database:** `data/p2_scene.db` (isolated)

**Product gap confirmed:** LLM enrichment (qwen3:14b) does not reliably follow scene ideas. Human shot design was necessary. This validates the human-edit workflow path.

**NEXT:** Generate Shot 1 using h3_r2v_v2 (character + environment, scene head)
