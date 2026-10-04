# CIP — Crisis Intelligence Platform Frontend Architecture

## Authoritative Frontend

The sole authoritative production frontend for the Crisis Intelligence Platform (CIP) is located in:
`src/frontend/static/index.html`

### Deployment & Serving
- **Production URL**: `https://cip-ruby.vercel.app/dashboard/` (with `/` redirecting to `/dashboard/`)
- **Server**: FastAPI `StaticFiles` mounted at `/dashboard` in `src/api/main.py`.
- **Runtime**: `@vercel/python` serverless runner configured in `vercel.json` and `api/index.py`.

### Functional Modules
All 10 required executive workflows are integrated and self-contained:
1. **Overview** (`#view-overview`): Executive summary, CRI score, recent developments, leadership inquiry paths.
2. **Investigations** (`#view-investigations`): Grounded AI reasoning workspace, hypotheses, and evidence trails.
3. **Insights** (`#view-insights`): Plain-language prioritized insights with category and severity filters.
4. **Risks** (`#view-risks`): Multi-dimensional institutional risk matrix and breakdown.
5. **Predictions** (`#view-predictions`): On-demand forecast trajectories and confidence horizons.
6. **What-If** (`#view-whatif`): Grounded counterfactual scenario simulation (no hardcoded fallbacks).
7. **Evidence** (`#view-evidence`): Human-readable observations with expandable technical provenance tokens.
8. **Data** (`#view-data`): Multi-format upload, ingestion state tracker, and active dataset management.
9. **External Context** (`#view-osint`): External regulatory and sector developments without raw internal crawler telemetry.
10. **Profile** (`#view-profile`): User account, role verification, and persistent institutional workspace selection.
