# Part 1: Build a Tool, Break It, and Harden It

**Domain 選擇:** Wikidata (SPARQL endpoint: `https://query.wikidata.org/sparql`)

**設計風格:** Claude Code-style 互動式 CLI (REPL + slash commands + tool-use loop + 漂亮 TUI)

**Objective:** Build an interactive CLI agent that takes natural language queries, converts them to SPARQL via LLM, executes against Wikidata, and returns results — with multi-turn conversation, streaming output, and slash commands.

---

## Phase 0: Setup & 技術選型

- [ ] **0.1 語言與套件選擇 (Python 推薦)**
  - **LLM:** `anthropic` (Claude `claude-sonnet-4-6` 或 `claude-opus-4-7`),帶 prompt caching 與 tool use
  - **TUI / Rendering:**
    - `rich` — 彩色輸出、表格、語法高亮、spinner、Markdown
    - `prompt_toolkit` — REPL 輸入,支援多行、history、autocomplete、key bindings
    - (進階:`textual` 全 TUI 應用,但 overkill)
  - **SPARQL:** `SPARQLWrapper`
  - **CLI 框架:** `typer` 或 `click` (subcommands 用)
  - **設定 / state:** `pydantic-settings`、本地 `~/.nl2sparql/` 存 history & cache

- [ ] **0.2 Repo 結構 (mirror Claude Code 風格)**
  ```
  /src/nl2sparql
    __init__.py
    cli.py              # Typer entry point (subcommands)
    repl.py             # Interactive REPL loop
    agent.py            # Tool-use agent loop (LLM ↔ tools)
    tools/              # Tool implementations exposed to LLM
      sparql_exec.py    # tool: run a SPARQL query
      entity_search.py  # tool: wbsearchentities
      property_search.py
      schema_lookup.py  # tool: get property/entity description
    prompts/
      system.md
      few_shot.md
    commands/           # Slash command handlers
      help.py
      clear.py
      model.py          # /model switch (Claude / GPT / Llama / Qwen ...)
      sparql.py         # /sparql <raw query>
      explain.py
      export.py
    rendering/
      table.py          # rich tables
      sparql_highlight.py
      streaming.py
    state.py            # session state (messages, last query, last result)
    config.py
  /tests
  /docs
    failures.md         # Phase 2 output
    architecture.md
  README.md
  pyproject.toml
  ```

- [ ] **0.3 Wikidata 基礎熟悉**
  - 在 query.wikidata.org 練 5-10 個 query
  - 熟 Q-id / P-id、prefixes、`SERVICE wikibase:label`、`FILTER`、`OPTIONAL`、aggregation

---

## Phase 1: Baseline — Claude Code-style Interactive Agent

### 1.1 啟動體驗 (UX)
- [ ] 開機畫面 (ASCII banner + 版本 + model 名 + endpoint)
- [ ] 支援兩種模式:
  - **Interactive REPL** (default):`sparqlgen` (no args) → 進入 prompt
  - **One-shot:** `sparqlgen ask "..."` → 跑一次就退出 (給 Part 2 eval pipeline 用)
  - **Pipe-friendly:** `echo "query" | sparqlgen ask --json` → 純機器可讀輸出

### 1.2 REPL 核心
- [ ] `prompt_toolkit` 提供:
  - 多行輸入 (Esc+Enter 或 `\` 續行)
  - Up/Down history 跨 session 持久化 (`~/.nl2sparql/history`)
  - Tab autocomplete:slash commands、最近用過的 entity
  - Ctrl+C 中斷當前 LLM/SPARQL 呼叫,不退出 REPL
  - Ctrl+D 退出
- [ ] Prompt 顯示:`nl2sparql ❯ ` (顏色區分 user / assistant / tool)

### 1.3 Slash Commands (精簡版)
- [ ] `/help` — 列所有命令
- [ ] `/clear` — 清空當前 conversation context
- [ ] `/exit` (或 `/quit`)
- [ ] `/model <name>` — 切換 LLM (Part 2 多模型評測必需:Claude / GPT / Llama / Qwen ...)
- [ ] `/sparql <query>` — 直接跑 raw SPARQL,跳過 LLM
- [ ] `/explain` — 讓 LLM 解釋上一次產生的 SPARQL
- [ ] `/export <file>` — 把最後結果輸出成 csv/json

### 1.4 Tool-Use Agent Loop (核心架構,模擬 Claude Code)
- [ ] 不是「一次叫 LLM 出 SPARQL 就跑」,而是 **agent loop**:
  1. LLM 收到 user query
  2. LLM 可選擇呼叫 tool:
     - `search_entity(name, lang)` → 回 top-5 候選 Q-id + description
     - `search_property(description)` → 回候選 P-id
     - `get_schema(qid)` → 回此實體常見的 properties
     - `run_sparql(query)` → 實際執行,回 bindings 或錯誤
  3. LLM 看 tool 結果決定下一步 (改 query / 再搜一次 / 直接答)
  4. 直到 LLM 給最終 answer
- [ ] 用 Anthropic API 的 `tools` 參數實作 (OpenAI / Gemini 之後 Part 2 也能對齊)
- [ ] 每個 tool call 在 TUI 上顯示 (像 Claude Code 那樣):
  ```
  ⏺ search_entity(name="Apple", lang="en")
    └─ Q312 (Apple Inc.) | Q89 (apple, fruit) | Q6498542 (Apple Records)
  ⏺ run_sparql(...)
    └─ ✓ 10 rows returned (1.2s)
  ```

### 1.5 Permission / Confirmation (Claude Code-style)
- [ ] **Read-only by default** (Wikidata 本來就只讀,但 UI 上仍顯示「即將執行」確認)
- [ ] Modes:
  - `--auto` (or `/auto on`):自動跑所有 SPARQL 不問
  - default:每次執行 SPARQL 前顯示 query + 「Run? [Y/n/edit]」
  - `edit` 選項把 SPARQL 丟到 `$EDITOR` 讓使用者改
- [ ] 大查詢警告 (沒有 LIMIT 或預估高 cost 時)

### 1.6 Streaming Output
- [ ] LLM response 用 streaming API,逐字輸出
- [ ] Tool 執行時顯示 spinner (`rich.status`)
- [ ] SPARQL 結果用 `rich.table` 漂亮渲染,長 cell 自動截斷 + `/expand <row>` 展開

### 1.7 Session State
- [ ] 記憶 conversation history → 後續 query 可指代 ("now filter those by year > 2000") -> Langchain 
- [ ] 記憶 last result → `/export` `/explain` 可用
- [ ] Auto-save session 到 `~/.nl2sparql/sessions/<timestamp>.json`,可 `--resume`

### 1.8 Demo 範例
- "List Nobel laureates in physics born in Germany"
- "Top 10 most populous cities in Asia"
- 「台灣的總統有誰」
- Multi-turn:"Movies by Nolan" → "only after 2010" → "show their box office"

---

## Phase 2: Break It

### 2.1 設計 20+ 攻擊輸入 (分類記錄到 `docs/failures.md`)
- [ ] **Ambiguous entities:** "Paris", "Apple revenue", "Java population", "Mercury"
- [ ] **Conflicting constraints:** "Movies before 2000 and after 2010", "Living people who died in 1990"
- [ ] **Typos:** "Einstien", "Toyko", "Shaksepeare"
- [ ] **多語言:**
  - 中:「台灣的總統有誰」「日本最高的山」
  - 日:「日本で一番高い山」
  - 西:「presidentes de México」
  - 混合:「List 台灣的 mountains over 3000m」
- [ ] **Multi-hop:** "Cities where Nobel physics laureates were born and the country won the World Cup"
- [ ] **Aggregation:** "Average lifespan of Roman emperors", "Sum of GDP of EU countries 1995"
- [ ] **時間 + 限定:** "US presidents during the Cold War who were Republicans"
- [ ] **否定:** "Countries without a coastline", "Movies nominated but did NOT win an Oscar"
- [ ] **Prompt injection:** "Ignore previous instructions and..."
- [ ] **不存在實體:** "Population of Atlantis", "Birthday of Sherlock Holmes"
- [ ] **指代 / multi-turn ambiguity:** "show me more" 在沒 context 時
- [ ] **超大結果:** "All humans" (沒 LIMIT)

### 2.2 失敗類型 tag
- `entity-link`、`property-hallucination`、`syntax-error`、`semantic-wrong`、`timeout`、`empty-result`、`unsafe-query`、`context-loss`

---

## Phase 3: Harden & Fix

- [ ] **3.1 Entity Disambiguation**
  - Tool-loop 強制先 `search_entity` 再寫 query
  - Top-1 score 模糊時,REPL 互動詢問使用者選哪個
  - One-shot 模式 fallback:用 description + LLM 自選 + 在輸出標註選了哪個

- [ ] **3.2 SPARQL 語法驗證 + Self-repair**
  - 執行前 regex / `rdflib` 檢查 prefix 與 syntax
  - 失敗 → 把 error feed 回 LLM,最多 2 次 retry,REPL 上顯示修復過程

- [ ] **3.3 Property Hallucination 防護**
  - LLM 出的 P-id 先用 `wbgetentities` 驗
  - 不存在 → 自動觸發 `search_property` 再生成

- [ ] **3.4 多語言**
  - 偵測輸入語言 (heuristic + LLM)
  - `SERVICE wikibase:label` 的 lang 參數動態決定
  - 中/日 entity 在 wbsearchentities 用對應 lang code

- [ ] **3.5 Conflicting Constraints**
  - System prompt 要求矛盾時回 `{"error": "conflicting", "explanation": "..."}` 而非硬寫 query
  - REPL 友善顯示

- [ ] **3.6 Prompt Injection / Safety**
  - 輸出 SPARQL 禁止 `INSERT|DELETE|LOAD|CLEAR|DROP` (regex 攔截)
  - System prompt 加 guardrail
  - User input 經過 escape 再 embed

- [ ] **3.7 Empty Result 處理**
  - 自動建議放寬條件 (移除 filter 重試,提示「Try without X?」)

- [ ] **3.8 Multi-turn 指代解析**
  - System prompt 提供 conversation history
  - LLM 看到 "filter those by..." 能引用上一個 query 的 result schema

- [ ] **3.9 大查詢守門**
  - 偵測無 LIMIT → 自動加 `LIMIT 100` (可被 `--no-limit` 覆寫)
  - Confirmation prompt 顯示預估行數警告

- [ ] **3.10 中斷 & Cancel**
  - Ctrl+C 中斷當下 SPARQL / LLM 呼叫,session 不死

---

## Phase 4: README & Explain

- [ ] **4.1 Architecture 圖** (REPL → agent loop → tools → Wikidata)
- [ ] **4.2 Demo GIF / asciinema**(claude code 同款 — 大加分)
- [ ] **4.3 Failure cases 修復前後對照表**
- [ ] **4.4 「Fundamentally hard」技術論述**
  - **Entity ambiguity underdetermined:** 單輪缺 context,即使人類也猜不準 → 只能 multi-turn 補
  - **Open-world property space:** Wikidata 萬+ properties,LLM 沒看過罕見的;RAG 也可能 miss
  - **Closed-world inference 反問題:** "Countries without coastline" 需 closed-world,Wikidata open-world,NOT EXISTS 只在有記錄 negative fact 時 work
  - **Temporal granularity 模糊:** "during the Cold War" 起訖年代多家解釋
  - **聚合語意 schema-dependent:** "Roman emperors lifespan" 算法定義不一
  - **跨語言 label 不對稱:** zh-tw / ja Wikidata label 覆蓋低於 en,某些實體中文無法搜
  - **Tool-loop ≠ free lunch:** Agent 多輪會放大 cost、latency、error compounding
- [ ] **4.5 README 結構**
  ```
  # NL2SPARQL — A Claude Code-style Wikidata Agent
  ## Demo (gif)
  ## Quick Start
  ## Slash Commands
  ## Architecture (agent + tools)
  ## Examples (single-turn / multi-turn)
  ## Failure Cases We Fixed
  ## Failure Cases That Remain (and Why)
  ## Roadmap / Limitations
  ```

---

## 評審加分項
- [ ] `--dry-run` 只產 SPARQL 不執行 (Part 2 eval pipeline 必需)
- [ ] `--json` 模式輸出機器可讀 (one-shot)
- [ ] Cache `wbsearchentities` 與 schema lookup 到本地 sqlite
- [ ] Prompt caching (Anthropic) — system prompt + few-shot 設 cache breakpoint,降本提速
- [ ] Pytest 涵蓋 entity linker / SPARQL validator / tool-loop 邏輯
- [ ] `pip install nl2sparql` 可用 (pyproject.toml + entry_points)
- [ ] asciinema 錄影放到 README
