# System Architecture

How a reverse-split announcement becomes a dry-run order. Full detail on every stage
is in [REPORT.md](../REPORT.md); this is the shape of the pipeline, not the internals.

```mermaid
flowchart TD
    classDef source fill:#e8eef7,stroke:#4a6fa5,color:#1a2a3a
    classDef storage fill:#fdf3e0,stroke:#c9922e,color:#3a2a0a
    classDef research fill:#f2eaf7,stroke:#8a5aa8,color:#2a1a3a
    classDef live fill:#fde9e9,stroke:#b8494f,color:#3a1414
    classDef terminal fill:#e4e4e4,stroke:#666,color:#1a1a1a

    A["Scrapers + SEC EDGAR<br/>(nightly, 5am EST)"]:::source
    B[("MongoDB<br/>confirmed splits")]:::storage
    C["Offline research<br/>backtest + walk-forward"]:::research
    D["Daily signal generator"]:::live
    E{"dry-run<br/>or --live?"}:::live
    F["Log + SMS alert"]:::terminal
    G["Real Schwab order"]:::terminal

    A --> B --> D
    C -. "strategy params" .-> D
    B -. "historical data" .-> C
    D --> E
    E -->|dry-run, default| F
    E -->|"--live"| G
```

**The one thing to remember about this diagram:** research (purple) only flows *into*
live trading (red) as a fixed set of parameters — it doesn't run automatically or adapt
on its own. Changing the strategy means re-running the research and manually updating
those params.
