# Техническое задание и roadmap
## Local AI Film Director / Production Orchestrator

**Статус:** архитектурный draft v1.0  
**Тип проекта:** локальная AI production platform  
**Целевая ОС:** Windows 11  
**Основной GPU target:** NVIDIA RTX 5090 32 GB  
**Основной video backend:** ComfyUI  
**Основная video model:** MiniMax H3  
**Основной LLM layer:** локальная OpenAI-compatible модель через LM Studio или Ollama  
**Основные архитектурные references:** Wind Comic, KupkaProd Cinema Pipeline, AI Video Production Editor, ComfyUI Cinema Pipeline

---

# 1. Цель проекта

Создать локальную production system, которая позволяет пользователю начать с простой идеи:

> «Детектив ночью приезжает в заброшенную психиатрическую больницу и встречает там девочку».

и пройти управляемый процесс:

```text
IDEA
 ↓
STORY
 ↓
DIRECTOR TREATMENT
 ↓
CHARACTERS
 ↓
STYLE BIBLE
 ↓
SCENES
 ↓
BEATS
 ↓
COVERAGE PLAN
 ↓
SHOT LIST
 ↓
STORYBOARD
 ↓
SHOT SPECIFICATION
 ↓
REFERENCE PREPARATION
 ↓
COMFYUI
 ↓
MINIMAX H3
 ↓
MULTIPLE TAKES
 ↓
REVIEW
 ↓
REGENERATION
 ↓
CONTINUITY
 ↓
APPROVED SHOTS
 ↓
TIMELINE
 ↓
AUDIO
 ↓
EXPORT
```

Система не должна требовать от пользователя заранее знать, как профессионально строить AI-фильм.

Она должна самостоятельно предлагать структуру производства, но позволять пользователю вмешаться на любом этапе.

---

# 2. Главная концепция продукта

Система не должна быть:

> «ещё одним prompt-to-video генератором».

Система должна быть:

> **AI Director + Production Manager + ComfyUI Orchestrator.**

Минимальная концепция:

```text
                USER
                  |
                  v
          AI DIRECTOR LAYER
                  |
                  v
        PRODUCTION SPECIFICATION
                  |
                  v
         CONTINUITY / SHOT LAYER
                  |
                  v
        COMFYUI ORCHESTRATION
                  |
                  v
             MINIMAX H3
                  |
                  v
         TAKES / REVIEW / EDIT
```

---

# 3. Что система НЕ должна делать в первой версии

Следующие функции намеренно исключаются из MVP:

1. Собственный видеогенератор.
2. Собственный image generation engine.
3. Собственный NLE уровня DaVinci Resolve.
4. Полная замена Wind Comic.
5. Полная замена ComfyUI.
6. Автоматический монтаж без человеческого контроля.
7. Автоматический rejection generated videos.
8. Поддержка десятков video providers одновременно.
9. Полноценная distributed GPU orchestration.
10. Мобильный интерфейс.
11. Multiplayer collaboration.
12. Cloud deployment.

---

# 4. Основная архитектура

```text
┌──────────────────────────────────────────────┐
│                    UI                        │
│                                              │
│ Project                                      │
│ Story                                       │
│ Characters                                  │
│ Style                                       │
│ Scenes                                      │
│ Storyboard                                  │
│ Shots                                       │
│ Generation                                  │
│ Review                                      │
│ Timeline                                    │
│ Export                                      │
└─────────────────────┬────────────────────────┘
                      │
                      v
┌──────────────────────────────────────────────┐
│                DIRECTOR LAYER                │
│                                              │
│ Writer                                      │
│ Director                                    │
│ Character Designer                          │
│ Scene Designer                              │
│ Storyboard Planner                          │
│ Coverage Planner                            │
│ Continuity Advisor                          │
│ Review Advisor                              │
└─────────────────────┬────────────────────────┘
                      │
                      v
┌──────────────────────────────────────────────┐
│          PRODUCTION SPECIFICATION            │
│                                              │
│ Project → Sequence → Scene → Beat → Shot    │
│                                              │
│ References / Camera / Action / Audio        │
│ Continuity / Generation Strategy             │
└─────────────────────┬────────────────────────┘
                      │
                      v
┌──────────────────────────────────────────────┐
│             ORCHESTRATION LAYER              │
│                                              │
│ Shot Manager                                │
│ Queue Manager                               │
│ Workflow Registry                           │
│ Reference Manager                            │
│ Take Manager                                │
│ Review Manager                              │
└─────────────────────┬────────────────────────┘
                      │
                      v
┌──────────────────────────────────────────────┐
│                  COMFYUI                     │
│                                              │
│ H3 T2V                                      │
│ H3 I2V                                      │
│ H3 R2V                                      │
│ H3 First/Last                              │
│ Image workflows                             │
│ Upscale workflows                           │
└─────────────────────┬────────────────────────┘
                      │
                      v
                    MEDIA
```

---

# 5. Архитектурный принцип №1 — Production Graph

Все сущности должны образовывать dependency graph.

Пример:

```text
PROJECT
  |
  └── CHARACTER 01
         |
         └── SCENE 03
                |
                └── BEAT 04
                       |
                       └── SHOT 03_04_02
                              |
                              ├── STORYBOARD
                              ├── REFERENCES
                              ├── H3 PROMPT
                              ├── TAKE 01
                              ├── TAKE 02
                              └── REVIEW
```

Изменение Character 01 не должно автоматически уничтожать результаты.

Система должна показать:

```text
Affected:
Scene 03
Scene 04
Scene 06

Potentially outdated:
Shot 03_04_02
Shot 04_01_01
Shot 06_03_02
```

Пользователь принимает решение о regeneration.

---

# 6. Архитектурный принцип №2 — Artifact-first

Каждый этап обязан создавать постоянный artifact.

Минимальный набор:

```text
story.json
director_treatment.json
characters.json
style_bible.json
scenes.json
beats.json
coverage.json
storyboard.json
shots.json
references.json
h3_prompts.json
takes.json
reviews.json
continuity.json
timeline.json
project.json
```

Также сохраняются media assets:

```text
references/
storyboards/
keyframes/
videos/
audio/
exports/
```

---

# 7. Архитектурный принцип №3 — Model-agnostic Director

Director layer не должен знать, что существует MiniMax H3.

Он должен знать:

```text
generate_video
generate_image
generate_reference
extend_video
```

Конкретный model provider подключается через adapter.

---

# 8. Model Adapter API

Обязательный интерфейс:

```python
class VideoEngine:
    def validate(self, request): ...
    def submit(self, request): ...
    def status(self, job_id): ...
    def cancel(self, job_id): ...
    def result(self, job_id): ...
```

Первый backend:

```text
ComfyUIEngine
```

Позже:

```text
LTXEngine
H3CloudEngine
KlingEngine
VeoEngine
```

---

# 9. ComfyUI Adapter

## Задача

Связать production system с существующим локальным ComfyUI API.

## Функции

1. Проверка подключения.
2. Получение queue status.
3. Отправка workflow.
4. Передача inputs.
5. Мониторинг execution.
6. Получение output.
7. Сохранение результата.
8. Связывание результата с Shot/Take.
9. Обработка ошибок.
10. Retry.

## Done criteria

Этап считается готовым, когда система:

- подключается к `127.0.0.1:8188`;
- отправляет тестовый workflow;
- получает image;
- получает video;
- знает, к какому Shot относится result;
- переживает ComfyUI restart;
- не теряет state при ошибке.

---

# 10. Workflow Registry

Workflow не должен быть зашит в Python/TypeScript код.

Структура:

```text
/workflows/
    minimax_h3/
        t2v.json
        i2v.json
        r2v.json
        first_last.json
```

Каждый workflow имеет metadata:

```json
{
  "id": "h3_i2v_v1",
  "engine": "comfyui",
  "model": "minimax_h3",
  "type": "i2v",
  "inputs": {},
  "outputs": {}
}
```

Также:

```json
{
  "resolution_min": {},
  "resolution_max": {},
  "duration_min": 0,
  "duration_max": 0,
  "vram_estimate": 0
}
```

---

# 11. Phase 0 — Discovery / Technical Spike

## Цель

Не программировать production system до проверки существующих компонентов.

## Задачи

### 0.1. Проверить Wind Comic

Проверить:

- локальный запуск;
- OpenAI-compatible LLM;
- Qwen;
- Ollama;
- ComfyUI provider;
- project artifacts;
- storyboard format;
- shot metadata;
- API/internal interfaces;
- export formats.

Wind Comic актуально заявляет provider-agnostic архитектуру, local LLM через OpenAI-compatible endpoint и ComfyUI provider.

### 0.2. Проверить KupkaProd

Запустить локально.

Проверить:

- script analysis;
- character extraction;
- storyboard;
- ComfyUI connection;
- take generation;
- resume;
- project state.

KupkaProd уже реализует именно этот цикл локально, но штатный video pipeline сейчас ориентирован на LTX-AV.

### 0.3. Проверить H3

Установить:

- официальный workflow;
- один актуальный community workflow;
- H3 T2V;
- H3 I2V;
- H3 R2V;
- First/Last workflow.

Актуальные community workflows уже охватывают T2V, I2V, First/Last, Reference и audio-oriented сценарии.

### 0.4. Проверить H3 performance

Сделать benchmark:

```text
720p
~1 MP
5 sec
10 sec
15 sec
```

Для каждого:

- VRAM;
- время;
- peak RAM;
- disk;
- startup;
- model load;
- generation time.

Текущие community reports показывают, что даже на 16 GB локальная H3 production уже возможна, но реальные времена генерации зависят от workflow/model/acceleration.

## Deliverables

```text
TECHNICAL_SPIKE.md
COMPONENT_MATRIX.md
H3_BENCHMARK.md
WIND_COMIC_INTEGRATION.md
COMFYUI_INTEGRATION.md
```

## Definition of Done

Phase 0 завершён только после ответа на вопросы:

1. Что можно использовать напрямую?
2. Что требует adapter?
3. Что придётся писать самим?
4. Какой H3 workflow является baseline?
5. Какой формат данных проходит между Director и Production?
6. Какой минимум нужно реализовать для первого фильма?

---

# 12. Phase 1 — Project Core

## Цель

Создать стабильную локальную модель проекта.

## Задачи

### 1.1. Project model

Создать:

```text
Project
Sequence
Scene
Beat
Shot
Take
Reference
Review
```

### 1.2. IDs

Все сущности получают immutable IDs.

Например:

```text
project_001
scene_003
beat_003_04
shot_003_04_02
take_003_04_02_01
```

### 1.3. Versioning

Сохранять:

```text
created_at
updated_at
version
parent_version
```

### 1.4. Persistence

На MVP использовать SQLite + файловую систему.

Не использовать cloud database.

## Done criteria

После перезапуска приложения:

- все проекты сохраняются;
- все shots остаются;
- media paths корректны;
- generation state не теряется.

---

# 13. Phase 2 — Local LLM Layer

## Цель

Подключить локальный мозг.

## Поддержка

Первично:

```text
LM Studio
```

Потом:

```text
Ollama
```

Endpoint:

```text
http://127.0.0.1:1234/v1
```

или соответствующий endpoint Ollama.

## Требования

LLM adapter должен поддерживать:

- chat;
- JSON output;
- retries;
- temperature;
- max tokens;
- structured schema;
- logging;
- model selection.

## Задачи

### 2.1. LLM provider interface

```python
class LLMProvider:
    def generate(...)
    def structured(...)
```

### 2.2. JSON validation

Любой agent output проходит:

```text
LLM
 ↓
JSON parser
 ↓
schema validator
 ↓
repair
 ↓
accept
```

### 2.3. Prompt templates

Prompt templates находятся вне кода:

```text
/prompts/
    writer/
    director/
    characters/
    scenes/
    storyboard/
    coverage/
    review/
```

## Done criteria

Qwen через LM Studio получает идею и возвращает валидный structured Project Proposal в 95/100 тестов без ручного исправления.

---

# 14. Phase 3 — Writer Agent

## Input

```text
one-line idea
```

## Output

```text
story
logline
genre
tone
theme
characters
beginning
middle
ending
```

## Не делать

Writer не генерирует H3 prompt.

## Done criteria

Для тестового набора из 20 идей:

- JSON valid;
- история имеет начало/конфликт/развязку;
- персонажи определены;
- нет противоречивых character facts.

---

# 15. Phase 4 — Director Agent

## Input

Story.

## Output

Director Treatment:

```text
visual language
cinematography
pacing
editing style
sound strategy
camera language
lighting
shot density
```

## Done criteria

Director Treatment существует независимо от generation engine.

---

# 16. Phase 5 — Character Bible

## Input

Story + Director Treatment.

## Output

Каждый персонаж:

```text
identity
age
appearance
face
hair
body
clothing
props
visual anchors
behavior
emotional baseline
```

## Asset generation

Создать reference images через ComfyUI.

Минимум:

```text
front
3/4
side
full body
close-up
```

## Done criteria

Пользователь видит Character Board и может:

```text
Approve
Regenerate
Edit
```

---

# 17. Phase 6 — Style Bible

## Output

```text
genre
visual style
color
lighting
camera
lens language
texture
production design
references
```

Style Bible применяется ко всем shots по умолчанию.

## Done criteria

При создании нового shot style автоматически наследуется.

---

# 18. Phase 7 — Scene Breakdown

Нельзя сразу делать Shot List.

Pipeline:

```text
Story
 ↓
Sequence
 ↓
Scene
 ↓
Beat
```

## Scene содержит

```text
location
time
weather
characters
props
dramatic purpose
continuity state
```

## Beat содержит

```text
dramatic action
character intention
change
```

## Done criteria

Каждая сцена имеет:

- objective;
- characters;
- location;
- beats;
- beginning state;
- ending state.

---

# 19. Phase 8 — Coverage Planner

Это одна из ключевых функций.

Director должен определить:

```text
master shot
medium shots
close-ups
POV
reaction
insert
establishing
transition
```

Не каждая сцена обязана иметь все типы.

Пример:

```text
SCENE 03

Beat 1:
Detective enters.

Coverage:
SHOT 01 - wide master

Beat 2:
He hears sound.

Coverage:
SHOT 02 - medium rear
SHOT 03 - close-up reaction

Beat 3:
He looks down corridor.

Coverage:
SHOT 04 - POV

Beat 4:
Girl appears.

Coverage:
SHOT 05 - long reveal
SHOT 06 - detective reaction
```

Реальные H3 creators уже используют именно shot coverage и отдельные takes, а не пытаются получить полноценную сцену одним prompt.

## Done criteria

Для каждой Scene существует:

```text
coverage.json
```

в котором каждый Beat имеет хотя бы один Shot.

---

# 20. Phase 9 — Shot Specification

Каждый shot должен быть machine-readable.

Пример:

```json
{
  "shot_id": "SC03_SH05",
  "beat_id": "SC03_B04",

  "dramatic_purpose": "Girl reveal",

  "subjects": ["girl_01"],

  "action": {
    "description": "Girl stands motionless"
  },

  "environment": {
    "location": "hospital corridor"
  },

  "camera": {
    "shot_size": "long",
    "angle": "eye-level",
    "movement": "slow_push_in"
  },

  "lighting": {
    "description": "flickering fluorescent"
  },

  "audio": {
    "ambient": ["electrical hum"],
    "sfx": ["distant child laughter"]
  },

  "references": [],
  "duration": 6,
  "generation_strategy": "R2V"
}
```

## Done criteria

Shot specification может быть использован без человеческого переписывания для генерации H3 prompt.

---

# 21. Phase 10 — Storyboard

## Input

Shot Specifications.

## Output

Storyboard image per shot.

## UI

```text
Scene
 ├── Shot 01
 ├── Shot 02
 ├── Shot 03
 ├── Shot 04
 └── Shot 05
```

Каждый card:

- image;
- shot type;
- action;
- camera;
- duration;
- characters;
- status.

## Actions

```text
Approve
Reject
Regenerate
Edit
Duplicate
Move
```

## Done criteria

Пользователь может пройти весь фильм от начала до конца, просмотрев storyboard до запуска дорогостоящей video generation.

---

# 22. Phase 11 — Reference Manager

Система должна собирать references автоматически.

Для каждого Shot:

```text
Character references
Scene reference
Style reference
Previous-frame reference
Optional prop reference
```

## Reference priority

```text
1. direct shot reference
2. previous shot last frame
3. character reference
4. scene reference
5. style reference
```

Конфликты должны быть разрешаемыми вручную.

---

# 23. Phase 12 — H3 Prompt Builder

Только здесь появляется MiniMax H3.

Input:

```text
Shot Specification
+
Style Bible
+
Character DNA
+
Scene State
+
References
```

Output:

```text
H3 prompt
```

Prompt Builder должен быть заменяемым.

Не хранить его в одном огромном system prompt.

---

# 24. Phase 13 — Generation Strategy

Для каждого Shot система предлагает режим.

### T2V

Использовать, если:

- нет recurring character;
- нет сложного visual continuity;
- establishing shot.

### I2V

Использовать, если:

- композиция уже зафиксирована storyboard frame.

### R2V

Использовать, если:

- нужен character consistency;
- несколько references;
- recurring cast.

### First/Last

Использовать для:

- continuation;
- контролируемого перехода;
- start/end frame composition.

Актуальные H3 workflows и community tooling уже поддерживают эти варианты.

---

# 25. Phase 14 — ComfyUI Queue

## Job model

```text
Job
 ├── project_id
 ├── shot_id
 ├── workflow_id
 ├── parameters
 ├── status
 ├── seed
 ├── started_at
 └── finished_at
```

Статусы:

```text
PENDING
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
RETRYING
```

## Done criteria

Можно поставить в очередь 20 shots и после перезапуска приложения определить:

- что завершилось;
- что выполняется;
- что упало;
- что нужно retry.

---

# 26. Phase 15 — Take Manager

Для каждого Shot:

```text
Shot 05

Take 01
Take 02
Take 03
Take 04
```

Настройка:

```text
takes_per_shot = 3
```

## User actions

```text
Favorite
Reject
Approve
Duplicate
Regenerate
```

## Done criteria

Одновременно существуют несколько результатов одного Shot, и пользователь может выбрать один approved Take.

---

# 27. Phase 16 — Continuity Manager

Это один из центральных компонентов системы.

## State

### Character State

```text
clothes
props
position
orientation
emotion
physical condition
```

### Environment State

```text
location
time
weather
lighting
objects
```

### Prop State

```text
object
location
condition
owner
```

### Narrative State

```text
what happened
who knows what
current objective
```

## После каждого approved Take:

```text
Shot N
 ↓
State Update
 ↓
Shot N+1 context
```

---

# 28. Phase 17 — Continuity Chain

Минимальная реализация:

```text
Shot N
 ↓
extract last frame
 ↓
Shot N+1
 ↓
use previous frame as continuation reference
```

Это не теоретическая функция: свежие H3 workflows уже реализуют автоматическую First/Last continuation, а отдельный Infinite Continuation Suite собирает длинные цепочки из H3 clips.

## Done criteria

Для 5 последовательных shots:

```text
Shot 01 → Shot 02 → Shot 03 → Shot 04 → Shot 05
```

система автоматически передаёт continuity references.

---

# 29. Phase 18 — AI Review

AI Review не имеет права автоматически удалять результат в MVP.

## Проверки

```text
Character consistency
Scene consistency
Prompt adherence
Motion quality
Composition
Continuity
Technical quality
```

Output:

```text
PASS
WARNING
FAIL
```

с пояснениями.

Пример:

```text
Character consistency: 9/10
Composition: 8/10
Prompt adherence: 6/10

Warnings:
- camera is moving faster than requested
- detective's flashlight disappeared
```

## Done criteria

AI reviewer формирует useful report минимум на 90% тестовых shots.

---

# 30. Phase 19 — Human Review

UI:

```text
┌─────────────────────────────────┐
│ Shot SC03_SH05                  │
│                                 │
│            VIDEO                │
│                                 │
├─────────────────────────────────┤
│ AI Review                       │
│                                 │
│ Character  8/10                 │
│ Motion     9/10                 │
│ Camera     6/10                 │
│                                 │
│ [Approve] [Reject] [Regenerate] │
└─────────────────────────────────┘
```

---

# 31. Phase 20 — Regeneration

При Regenerate пользователь должен иметь возможность выбрать:

```text
Same seed
New seed
Modify prompt
Modify references
Modify camera
Modify duration
Modify workflow
```

Также:

```text
Clone previous generation
```

чтобы экспериментировать с одним параметром.

---

# 32. Phase 21 — Smart Retry

Система не должна автоматически менять всё.

Пример AI review:

```text
Problem:
camera movement too fast
```

Предлагается:

```text
Retry strategy:
keep image
keep character refs
keep seed
change camera motion
```

User clicks:

```text
Regenerate
```

---

# 33. Phase 22 — Timeline Preparation

Не писать NLE.

Создать internal timeline:

```text
Scene 01
  Shot 01
  Shot 02
  Shot 03

Scene 02
  Shot 04
  Shot 05
```

Каждый clip имеет:

```text
start
duration
source
audio
transition
```

---

# 34. Phase 23 — Export

MVP:

```text
MP4
EDL/JSON
OTIO if feasible
```

Цель:

```text
Our system
 ↓
approved clips
 ↓
DaVinci Resolve
```

А не создание полноценного Resolve внутри продукта.

---

# 35. Phase 24 — Audio

Audio architecture:

```text
Dialogue
Voiceover
SFX
Ambience
Music
Generated H3 audio
```

В H3 native audio можно сохранить как отдельный media stream. Community examples уже используют H3 picture + stereo audio в одном pass.

Но audio system должен позволять заменять этот audio externally.

---

# 36. Phase 25 — Benchmarking

Создать стандартный benchmark project.

Например:

```text
3 scenes
5 characters
20 shots
60 takes
```

Собирать:

```text
generation_time
VRAM_peak
RAM_peak
failure_rate
retry_rate
approved_rate
continuity_score
```

---

# 37. Phase 26 — Performance Scheduler

ComfyUI jobs должны учитывать model memory.

Например:

```text
H3 R2V
H3 I2V
H3 T2V
```

не должны бездумно запускаться одновременно.

Concurrency должна быть configurable.

Wind Comic сам предупреждает, что высокая video concurrency может ухудшить keyframe-chain continuity и рекомендует низкую concurrency для связных shots.

## MVP

```text
video_concurrency = 1
```

Затем:

```text
1–2
```

после benchmark.

---

# 38. Phase 27 — Error Handling

Обязательные классы ошибок:

```text
ComfyUI unavailable
Workflow missing
Node missing
Model missing
VRAM OOM
LLM timeout
Invalid JSON
File missing
Generation timeout
Corrupt output
Unknown workflow parameter
```

Каждая ошибка должна давать:

```text
human-readable explanation
technical details
recommended action
retry option
```

---

# 39. Phase 28 — Recovery / Resume

После:

```text
PC restart
ComfyUI crash
application crash
generation failure
```

пользователь должен иметь:

```text
Resume Project
```

Система показывает:

```text
Completed: 14
Running: 1
Failed: 2
Pending: 7
```

---

# 40. Phase 29 — Logging

Логировать:

```text
LLM request
LLM response
workflow ID
prompt
seed
resolution
duration
ComfyUI job ID
error
asset path
```

Но не логировать секретные API keys.

---

# 41. Phase 30 — UI

Основное меню:

```text
PROJECT
STORY
CAST
STYLE
SCENES
STORYBOARD
SHOTS
GENERATE
REVIEW
TIMELINE
EXPORT
SETTINGS
```

---

# 42. Главная UX-страница

```text
PROJECT: Hospital

Progress

Story             ✓
Cast              ✓
Style Bible       ✓
Scenes            ✓
Coverage          ✓
Storyboard        ✓
Generation        18 / 31
Review             9 / 18
Continuity         7 / 18
Timeline           0 / 31
```

Каждый этап clickable.

---

# 43. Status Model

Каждый artifact должен иметь:

```text
DRAFT
READY
APPROVED
OUTDATED
FAILED
GENERATING
```

Например:

```text
Character v3 APPROVED

Shot 07:
OUTDATED

Reason:
Character v3 changed after Shot 07 was generated.
```

---

# 44. Phase 31 — Licensing Audit

Обязательно до релиза.

Проверить:

```text
Wind Comic
ComfyUI
custom nodes
H3 models
workflow authors
KupkaProd
third-party libraries
fonts
FFmpeg
```

Особое внимание:

KupkaProd сейчас явно указывает, что open-source project бесплатен для non-commercial use и commercial use требует отдельной лицензии.

AI Video Production Editor распространяется под GPL-3.0-or-later.

Поэтому никакой код из этих проектов нельзя просто копировать в закрытый коммерческий продукт без анализа лицензий.

---

# 45. Phase 32 — Security

Для local-only режима:

- default bind localhost;
- никакой внешней авторизации в MVP;
- API keys encrypted/local;
- не отправлять local project data наружу без явного provider choice;
- показывать, какие данные уходят во внешний API.

---

# 46. Phase 33 — Test Strategy

## Unit tests

Покрыть:

- schemas;
- project graph;
- dependency tracking;
- JSON repair;
- prompt building;
- state updates;
- job status.

## Integration tests

Проверить:

```text
LLM → Director
Director → Shot
Shot → ComfyUI
ComfyUI → Take
Take → Review
Review → Approved
Approved → Timeline
```

## End-to-end test

Одна тестовая история:

```text
2 characters
2 scenes
8 shots
```

должна пройти от идеи до MP4 без ручного редактирования JSON.

---

# 47. Definition of Done для каждой feature

Feature считается DONE только если выполнены все:

```text
[ ] code completed
[ ] unit tests
[ ] integration test if relevant
[ ] error handling
[ ] logging
[ ] UI state
[ ] documentation
[ ] data migration if needed
[ ] clean restart tested
[ ] Windows tested
```

---

# 48. Release Milestones

## M0 — Discovery

Результат:

```text
Architecture validated
Wind Comic tested
ComfyUI tested
H3 tested
```

### Exit criteria

Есть прототип:

```text
Idea
→ Wind Comic
→ storyboard
→ manual H3
```

---

# 49. M1 — Skeleton

Результат:

```text
Project
Scenes
Shots
Take model
ComfyUI connection
```

### Exit criteria

Можно вручную создать Shot и получить H3 video через UI.

---

# 50. M2 — Director MVP

Результат:

```text
Idea
→ Story
→ Characters
→ Style
→ Scenes
→ Beats
→ Coverage
→ Storyboard
```

### Exit criteria

Из одной идеи получается production-ready storyboard.

---

# 51. M3 — H3 Production MVP

Результат:

```text
Storyboard
→ Shot specification
→ H3 prompt
→ ComfyUI
→ Takes
```

### Exit criteria

20 test shots успешно генерируются из UI.

---

# 52. M4 — Continuity MVP

Результат:

```text
Shot N
→ state
→ Shot N+1
→ last frame/reference
```

### Exit criteria

5 connected shots проходят автоматически через continuity chain.

---

# 53. M5 — Review System

Результат:

```text
Generate
→ AI review
→ human approve/reject
→ regenerate
```

### Exit criteria

Плохой shot можно переделать независимо от остальных.

---

# 54. M6 — Production Run

Результат:

```text
3 scenes
20–30 shots
multiple takes
continuity
timeline
```

### Exit criteria

Система выпускает законченный короткий эпизод.

---

# 55. M7 — External Edit

Результат:

```text
approved takes
→ timeline
→ DaVinci Resolve
```

### Exit criteria

Из системы можно получить нормально организованные source clips + timeline metadata.

---

# 56. M8 — Hardening

Добавить:

- resume;
- crash recovery;
- job cancellation;
- retry;
- logs;
- benchmark;
- licensing report;
- settings;
- model profiles.

---

# 57. M9 — Production Beta

Условия:

- 3 разных тестовых фильма;
- минимум 100 generated shots;
- минимум 300 takes;
- recovery tests;
- continuity tests;
- repeatability tests.

---

# 58. Recommended development order

Разработчик не должен идти по принципу:

> «сделаю все UI».

Порядок:

```text
1. Spike
2. Project schema
3. ComfyUI adapter
4. H3 benchmark
5. LLM adapter
6. Writer
7. Director
8. Character/Style
9. Scene/Beat
10. Coverage
11. Shot schema
12. Storyboard
13. Reference manager
14. H3 prompt builder
15. Generation queue
16. Take manager
17. Continuity
18. Review
19. Regeneration
20. Timeline export
21. Audio
22. Hardening
```

---

# 59. Что делать с Wind Comic

Не fork сразу.

Сначала:

```text
Wind Comic
   ↓
investigate
   ↓
extract contracts / schemas
   ↓
prototype integration
```

Если его internal architecture удобно расширяется:

```text
использовать напрямую
```

Если нет:

```text
использовать как reference
```

Не писать свою копию Wind Comic до технической необходимости.

---

# 60. Что делать с KupkaProd

Не делать dependency.

Использовать:

- architecture reference;
- local pipeline ideas;
- ComfyUI orchestration ideas;
- review model;
- resume architecture;
- take management.

Причина — текущий license и LTX-specific implementation.

---

# 61. Что делать с Director's Console

Изучить UX и ComfyUI orchestration.

Он уже поддерживает storyboard/cinema workflow, gallery и несколько ComfyUI connections.

Использовать как reference для:

- gallery;
- storyboard canvas;
- multi-ComfyUI;
- project asset handling.

---

# 62. Что делать с AI Video Production Editor

Изучить:

```text
Director treatment
Scene wall
Storyboard
Continuity review
Re-film queue
Timeline
```

Это хороший reference для UX production loop.

---

# 63. Что делать с ComfyUI Cinema Pipeline

Изучить как отдельный advanced path.

Этот проект рассматривает ComfyUI именно как cinema production backend и даже использует Blender geometry → ControlNet для temporal/spatial consistency. Он также документирует MCP/ComfyUI/NLE integration.

Но Blender/ControlNet path не включать в MVP.

---

# 64. Advanced Phase — Spatial Continuity

После MVP:

```text
Storyboard
 ↓
Blender
 ↓
Camera
 ↓
Depth
 ↓
Control
 ↓
ComfyUI
 ↓
H3 / Wan / other
```

Это нужно для:

- сложной camera movement;
- spatial continuity;
- action scenes;
- repeatable blocking.

---

# 65. Advanced Phase — Multi-Engine Routing

После стабильного H3 pipeline:

```text
Shot
 ↓
AI evaluates requirements
 ↓
choose engine
```

Например:

```text
dialogue close-up → H3
camera-heavy action → model X
stylized shot → model Y
background extension → model Z
```

Но это Phase 2+.

---

# 66. Advanced Phase — Distributed Rendering

Только после доказательства production value:

```text
Director
   |
Orchestrator
   |
 ┌─┴───────┬─────────┐
GPU1      GPU2      GPU3
ComfyUI   ComfyUI   ComfyUI
```

Не делать до реальной необходимости.

---

# 67. Advanced Phase — Automatic Production Assistant

В будущем AI может говорить:

> Shot 12 rejected because the character's coat changed.

> Shot 13 needs a reaction shot.

> Scene 4 lacks an establishing shot.

> Shot 20 should inherit the last frame of Shot 19.

Это уже настоящий AI Director Assistant.

---

# 68. Final acceptance test

Проект считается MVP COMPLETE, если новый пользователь способен:

### Step 1

Открыть приложение.

### Step 2

Создать проект:

> «Психологический хоррор, 2 минуты».

### Step 3

Получить:

```text
Story
Characters
Style
Scenes
Beats
Coverage
Storyboard
```

### Step 4

Утвердить storyboard.

### Step 5

Нажать:

```text
Generate Film
```

### Step 6

Система самостоятельно:

```text
creates shot specs
selects references
builds H3 prompts
submits ComfyUI workflows
tracks jobs
stores takes
```

### Step 7

Пользователь выбирает лучшие takes.

### Step 8

Система строит continuity chain.

### Step 9

Получается:

```text
approved clips
timeline
audio
export
```

### Step 10

Пользователь получает готовый набор:

```text
film.mp4
project.json
timeline.json
shots/
takes/
references/
```

---

# 69. Что считать настоящим успехом проекта

Не:

> «мы сделали красивый интерфейс».

Не:

> «H3 генерирует видео».

Не:

> «LLM написал сценарий».

Успех:

> **человек без глубокого знания AI-video production может начать с идеи и через управляемый pipeline получить связанный набор shots, из которых реально собирается фильм.**

Ключевой показатель:

```text
Idea
 ↓
Approved Storyboard
 ↓
Approved Shots
 ↓
Finished Sequence
```

при минимальном ручном вмешательстве.

---

# 70. Первый практический sprint

Первый sprint не должен быть «создать приложение».

Он должен быть экспериментом:

```text
IDEA
 ↓
Wind Comic
 ↓
Story
 ↓
Characters
 ↓
Storyboard
 ↓
EXPORT
 ↓
COMFYUI
 ↓
H3
 ↓
3 SHOTS
 ↓
LAST FRAME CONTINUATION
 ↓
EDIT
```

### Конкретный тестовый фильм

Создать сцену:

> мужчина ночью входит в заброшенную больницу, слышит детский смех и видит девочку в конце коридора.

Требования:

```text
2 characters
1 location
3 connected shots
5–10 sec each
H3
local generation
no cloud video API
```

Получить:

```text
Shot 01 — entrance
Shot 02 — hearing sound
Shot 03 — girl reveal
```

Затем проверить:

- character consistency;
- environment consistency;
- camera continuity;
- last-frame continuation;
- audio;
- монтаж.

Только после успешного теста переходить к программированию полной platform.

---

# 71. Главный принцип реализации

**Мы не строим новую AI-video ecosystem.**

Мы строим тонкий orchestration layer между уже существующими системами:

```text
          WIND COMIC
       Director / Story
              |
              v
       PRODUCTION SPEC
              |
              v
       OUR ORCHESTRATOR
              |
              v
          COMFYUI
              |
              v
         MINIMAX H3
              |
              v
      TAKES / REVIEW
              |
              v
        DAVINCI RESOLVE
```

Собственная разработка должна концентрироваться на том, чего сейчас не хватает:

**Production Specification + Continuity + orchestration + human review + state management.**

---

# 72. Roadmap в одной таблице

| Этап | Результат | Готово когда |
|---|---|---|
| M0 | Technical Spike | Wind/ComfyUI/H3 проверены |
| M1 | Project Core | проект устойчиво сохраняется |
| M2 | LLM Layer | локальный Qwen работает |
| M3 | Writer | idea → story |
| M4 | Director | story → treatment |
| M5 | Character/Style | visual bible |
| M6 | Scene/Beat | narrative breakdown |
| M7 | Coverage | production coverage |
| M8 | Storyboard | approved shot plan |
| M9 | Shot Spec | machine-readable shots |
| M10 | H3 Prompt | shot → H3 prompt |
| M11 | ComfyUI | shot → video |
| M12 | Takes | multiple generations |
| M13 | Continuity | connected shots |
| M14 | Review | AI + human review |
| M15 | Re-film | targeted regeneration |
| M16 | Timeline | approved sequence |
| M17 | Audio | final sound layer |
| M18 | Export | Resolve-ready output |
| M19 | Hardening | recovery + benchmark |
| M20 | Beta | complete short film |

---

# 73. Ключевое архитектурное правило

До M10 **не оптимизировать H3**.

До M13 **не делать сложную automation**.

До M15 **не делать automatic agent decisions**.

До M19 **не делать distributed rendering**.

И главное:

> **Если существующий проект уже решает задачу надёжно — не переписывать его.**

Сначала интегрировать.

Только когда integration становится bottleneck — писать собственный компонент.

Это позволит избежать ситуации, когда мы через месяц получим красивую 20-тысячную codebase, которая делает то же самое, что уже умеют Wind Comic + ComfyUI + существующие H3 workflows.

---

# 74. Рекомендуемый итоговый стек

```text
Frontend
Next.js / React
        ↓
Backend
Python FastAPI
        ↓
Project State
SQLite + filesystem
        ↓
LLM
LM Studio / Ollama
        ↓
Director
Wind Comic concepts / custom orchestration
        ↓
Generation
ComfyUI
        ↓
Video
MiniMax H3
        ↓
Post
FFmpeg
        ↓
NLE
DaVinci Resolve
```

Но конкретные технологии frontend/backend пока считаются **предварительными**. На M0 разработчик должен подтвердить, что они не создают лишнего дублирования с тем, что уже есть в Wind Comic или других используемых компонентах.

---

# 75. Финальный порядок работы для программиста

Программист должен идти именно так:

```text
DISCOVER
   ↓
VALIDATE
   ↓
INTEGRATE
   ↓
MEASURE
   ↓
DEFINE INTERFACES
   ↓
BUILD MVP
   ↓
TEST
   ↓
RUN REAL FILM
   ↓
IDENTIFY BOTTLENECKS
   ↓
ONLY THEN
WRITE CUSTOM COMPONENTS
```

Не наоборот.

Именно такой порядок сейчас наиболее рационален, потому что H3/ComfyUI ecosystem развивается очень быстро: буквально в августе 2026 появились новые Multishot, continuation и prompt/motion workflows, а реальные пользователи уже собирают 2–3-минутные локальные фильмы из десятков H3 clips.