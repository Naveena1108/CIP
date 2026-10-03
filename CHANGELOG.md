# Changelog — AI CRISS

All notable changes to the AI CRISS project are documented in this file.

## [1.3.0] - 2026-09-26 (Final System Integration & Validation)

### Fixed
- **DEF-01 (HIGH)**: Resolved `UnboundLocalError: cannot access local variable 'opening_rank'` in `SyntheticDataGenerator.generate_scenario` when generating the `ADMISSIONS_CRASH` / `ADMISSIONS_DECLINE` scenario.
- **DEF-02 (MEDIUM)**: Eliminated false-positive `CRITICAL` CET ranking anomaly on `HEALTHY` institutions caused by low historical rank variance ($\pm 2\%$ noise) by requiring material relative rank slippage ($\ge 15\%$) alongside $Z \ge 2.0$.
- **DEF-03 (HIGH)**: Added `TemporalAnomalyDetector.analyze_cross_signal` and non-linear cross-signal divergence amplification in `CrisisIntelligenceEngine` to detect decoupled crises (where strong/rising admissions and rankings mask severe placement collapse).
- **DEF-04 (MEDIUM)**: Expanded `SyntheticDataGenerator` and `/api/v1/ingest/synthetic` schema to support all 10 validation scenarios (`HEALTHY`, `ADMISSIONS_DECLINE`, `PLACEMENT_DETERIORATION`, `RANKING_DETERIORATION`, `CROSS_SIGNAL_CRISIS`, `CASCADING_CRISIS`, `GRADUAL_DECLINE`, `SUDDEN_CRISIS`, `RECOVERY`, `NOISY_MISSING_DATA`).

### Added
- Created `tests/test_e2e_scenarios.py` (10 end-to-end scenario tests validating the full pipeline from ingestion to binary PDF generation).
- Added `test_pdf_decompressed_content_verification` in `tests/test_reporting.py` to decompress ASCII85 + FlateDecode PDF streams and verify all institutional sections.
- Total automated test suite expanded to **63 passing tests (100% pass rate)**.

---

## [1.2.0] - 2026-09-25 (Implementation Gap Closure)

### Added
- Official Google Gen AI SDK (`google-genai` v2.25.0) integration in `src/engine/llm_reasoner.py` with structured output, temperature `0.0`, mathematical CRI tamper immunity, and deterministic offline fallback.
- Binary PDF report generation in `src/reporting/pdf_generator.py` using ReportLab Platypus (`v5.0.1`) and `GET /api/v1/institutions/{id}/report/pdf`.
- Next.js 14 App Router configuration and page suite in `src/frontend/`.

---

## [1.1.0] - 2026-09-24 (Operational & Final Readiness)

### Added
- Operational Readiness Review (ORR) and Final Readiness Review (FRR) validation suites.
- Production multi-stage `Dockerfile`, `docker-compose.yml`, and interactive cockpit at `/dashboard/`.

---

## [1.0.0] - 2026-08-28 (Core Intelligence Foundation)

### Added
- Canonical Pydantic v2 contracts, SQLAlchemy 2.0 async persistence, Excel/JSON adapters, feature extraction, anomaly detection, CRI scoring, trajectory predictor, and evidence dossier assembler.
