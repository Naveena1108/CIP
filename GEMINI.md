# AI CRISS — Autonomous Engineering Guidelines

## Architectural Rules (Strict Invariants)
1. **Decoupled Architecture**:
   `SOURCE -> ADAPTER -> CANONICAL CONTRACT -> VALIDATION -> FEATURES -> INTELLIGENCE`
2. **Zero Dataset Coupling**: The intelligence engine, anomaly detectors, and risk scoring calculators must NEVER depend directly on any specific CSV, Excel file, or external schema. All inputs must pass through a validated Adapter into a Canonical Contract.
3. **Dataset Agnosticism**: Real and synthetic datasets are treated identically through the Adapter layer.
4. **Test-Driven Development (TDD)**: Every calculation, anomaly threshold, and contract validator must have tests written BEFORE implementation code.
5. **Security & Type Safety**: All external inputs must be validated with Pydantic v2 schemas. No unvalidated dictionaries or untyped data passed to the engine.
