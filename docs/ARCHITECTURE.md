# System Architecture

This diagram shows the full system: how a reverse-split announcement becomes a
signal, how that signal became a strategy (offline research), and how a signal
becomes a dry-run order today.

See [REPORT.md](../REPORT.md) for the full narrative explanation of every stage.

```mermaid
flowchart TD
    classDef source fill:#e8eef7,stroke:#4a6fa5,color:#1a2a3a
    classDef pipeline fill:#eaf3ea,stroke:#4a8f5c,color:#1a2a1a
    classDef storage fill:#fdf3e0,stroke:#c9922e,color:#3a2a0a
    classDef research fill:#f2eaf7,stroke:#8a5aa8,color:#2a1a3a
    classDef live fill:#fde9e9,stroke:#b8494f,color:#3a1414
    classDef terminal fill:#e4e4e4,stroke:#666,color:#1a1a1a

    %% ---------- Sources ----------
    SA[StockAnalysis]:::source
    TR[TipRanks]:::source
    HF[HedgeFollow]:::source
    SEC1[SEC EDGAR<br/>submissions API]:::source
    SEC2[SEC EDGAR<br/>daily 8-K / 6-K feed]:::source

    %% ---------- Stage 1-2: discovery + confirmation ----------
    subgraph DISCOVERY["Stage 1-2 · Discovery &amp; Confirmation (nightly, 5am EST)"]
        direction TB
        RUNNER["scrapers/runner.py"]:::pipeline
        NIGHTLY["nightly_job.py<br/>backfill + Tier A/B scoring"]:::pipeline
        SCAN["scan_early_edgar.py<br/>keyword filter"]:::pipeline
        LLM["LLM classification<br/>gpt-4o-mini<br/>future? definitive? ratio?"]:::pipeline
    end

    SA --> RUNNER
    TR --> RUNNER
    HF --> RUNNER
    SEC1 --> NIGHTLY
    SEC2 --> SCAN --> LLM

    %% ---------- Storage ----------
    subgraph MONGO["MongoDB Atlas"]
        direction TB
        C1[("reverse_splits<br/>raw scraped events")]:::storage
        C2[("reverse_splits_edgar<br/>Tier A/B scored filings")]:::storage
        C3[("early_edgar_splits<br/>live signal source")]:::storage
    end

    RUNNER --> C1
    C1 --> NIGHTLY --> C2
    LLM --> C3

    %% ---------- Offline research ----------
    subgraph RESEARCH["Offline Research (run interactively, not scheduled)"]
        direction TB
        NEUT["engine.py<br/>neutralize_split()<br/>fixes the reverse-split<br/>price-jump bug"]:::research
        GRID["gridsearch.py<br/>4,620 permutations<br/>~12s, vectorized"]:::research
        WF["walkforward.py<br/>10 rolling OOS folds<br/>2 selection methods"]:::research
        RISK["tail_risk_test.py<br/>analyze_robustness.py<br/>borrow / bootstrap / capital caps"]:::research
        PARAMS["Chosen strategy params<br/>day_of_split · no stop · 20% TP"]:::research
    end

    C2 --> NEUT
    C3 --> NEUT
    NEUT --> GRID --> WF --> RISK --> PARAMS

    %% ---------- Live signal generation ----------
    subgraph SIGNAL["Stage 3 · Signal Generation (daily)"]
        direction TB
        GEN["generate_signals()"]:::live
        RANK["rank: ENTER_NOW &gt; confidence &gt; soonest exit"]:::live
        CAP["allocate capital<br/>vs. exposure cap"]:::live
        SHORT["shortability check<br/>proxy + real Schwab quote"]:::live
    end

    C3 --> GEN
    PARAMS -. "sizing / filter rules" .-> GEN
    GEN --> RANK --> CAP --> SHORT

    %% ---------- State ----------
    LEDGER[("open_positions ledger<br/>local JSON, per mode")]:::storage
    LEDGER -. "committed capital" .-> CAP

    %% ---------- Execution ----------
    subgraph EXEC["Stage 4 · Order Construction"]
        direction TB
        OM["OrderManager"]:::live
        MODE{"dry-run<br/>or --live ?"}:::live
    end

    SHORT --> OM --> MODE
    MODE -->|"dry-run (default)"| LOG["JSON log<br/>'would SELL_SHORT ...'"]:::terminal
    MODE -->|"--live"| SCHWAB["Schwab Trader API<br/>real order"]:::terminal
    OM -. "records new position" .-> LEDGER
    SHORT -. "logs real result" .-> GT[("shortability_ground_truth.csv<br/>proxy vs. reality")]:::storage

    %% ---------- Alerting ----------
    SMS["SMS<br/>carrier email-to-SMS gateway"]:::terminal
    GEN -->|"if any ENTER_NOW"| SMS

    %% ---------- Scheduling ----------
    GHA["GitHub Actions cron<br/>0 9 * * * UTC"]:::pipeline
    GHA -.->|triggers| RUNNER
    GHA -.->|triggers| NIGHTLY
    GHA -.->|triggers| SCAN
    GHA -.->|triggers| GEN
```

## Reading the diagram

- **Blue** — external data sources (scrapers, SEC EDGAR)
- **Green** — the nightly/daily automated pipeline (also used for the CI scheduler)
- **Orange** — persistent storage (MongoDB collections, local JSON/CSV state)
- **Purple** — offline research — run by hand, not on a schedule; its only output that
  feeds the live system is the *chosen strategy params* box
- **Red** — the live daily signal + order path
- **Grey** — terminal actions/outputs (a log, a real order, a text message)

## Two loops worth noticing

1. **The research loop feeds the live loop, not the other way around.** Walk-forward
   validation happens offline against historical data; its only export to the live
   system is a fixed set of parameters (hold rule, stop, take-profit, gap filter).
   Changing the live strategy means re-running the research loop and updating those
   params — it does not happen automatically.
2. **The shortability ground-truth loop is closing slowly, live.** Every signal run
   (dry-run included) logs the proxy classifier's guess next to Schwab's real answer.
   That file is how the proxy gets calibrated over time — it's the only part of this
   diagram that learns from live operation.
