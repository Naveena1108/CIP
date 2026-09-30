# AI CRISS — System Operations Manual

**Document ID**: `AI-CRISS-SOP-001`  
**Classification**: Operational Standard Operating Procedure (SOP)  
**Version**: `1.0.0`  
**Date**: September 24, 2026  

---

## 1. System Overview & Architecture

AI CRISS is a cross-signal crisis intelligence platform designed for higher education institutions. The system operates as a **High-Cohesion Modular Monolith** enforcing strict unidirectional boundaries:

$$\text{DATA} \longrightarrow \text{FEATURES} \longrightarrow \text{INTELLIGENCE} \longrightarrow \text{PREDICTION} \longrightarrow \text{EVIDENCE} \longrightarrow \text{LLM} \longrightarrow \text{API} \longrightarrow \text{UI}$$

### Core Service Ports & Endpoints
- **API Base URL**: `http://localhost:8000`
- **Interactive Cockpit**: `http://localhost:8000/dashboard/`
- **Interactive API Documentation (Swagger)**: `http://localhost:8000/docs`
- **Operational Health Check**: `http://localhost:8000/health`

---

## 2. Environment Configuration

All operational configurations are governed through environment variables:

| Variable | Default Value | Description | Production Guidance |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./ai_criss.db` | SQLAlchemy async connection URI | Set to `postgresql+asyncpg://user:pass@host:5432/aicriss` for enterprise clusters |
| `JWT_SECRET_KEY` | *(Built-in development key)* | Secret key for HS256 JWT signature | Generate using `openssl rand -hex 32` |
| `PORT` | `8000` | HTTP listen port | Bind to internal reverse proxy port |
| `HOST` | `0.0.0.0` | Network binding interface | Use `127.0.0.1` if behind NGINX / Cloudflare |
| `GEMINI_API_KEY` | *(Optional)* | Google Gemini LLM API key | Optional; system includes 100% deterministic template fallback |

---

## 3. Deployment, Startup & Shutdown Procedures

### 3.1 Local / Virtual Environment Execution
```bash
# 1. Activate dedicated virtual environment
.\.venv\Scripts\Activate.ps1

# 2. Start the production ASGI server
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

### 3.2 Containerized Docker Deployment
```bash
# Build and run the multi-stage production container
docker-compose up -d --build

# View real-time container logs
docker-compose logs -f api

# Stop and gracefully terminate
docker-compose down
```

### 3.3 Graceful Shutdown Lifecycle
Upon receiving `SIGTERM` or `SIGINT`, FastAPI executes the lifespan shutdown context:
1. Stops accepting incoming HTTP requests.
2. Waits for in-flight requests to complete (within 10s grace period).
3. Flushes and closes all SQLAlchemy connection pools.
4. Terminates cleanly with exit code `0`.

---

## 4. Role-Based Access Control (RBAC) & User Management

AI CRISS enforces strict principle-of-least-privilege RBAC:

| Role | Permissions | Intended Users |
| :--- | :--- | :--- |
| **SuperAdmin** | Full system access, user provisioning, database backups | IT Administrators, Chief Technology Officers |
| **Auditor** | Upload Excel files, view institutional dossiers, run evaluations | Accreditation Reviewers, Compliance Officers |
| **Analyst** | Trigger synthetic simulations, run scenario sliders, view trends | Institutional Researchers, Strategic Planners |
| **Viewer** | Read-only access to dashboard cockpit and published reports | Department Heads, Deans, Governing Board |

### User Registration Workflow
```bash
curl -X POST "http://localhost:8000/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "auditor_01",
    "email": "auditor@university.edu",
    "password": "StrongPassword2026!",
    "role": "Auditor"
  }'
```

### Authentication & Token Retrieval
```bash
curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=auditor@university.edu&password=StrongPassword2026!"
```

---

## 5. End-to-End Operational Workflows

### Workflow 1: Institutional Excel Workbook Ingestion
1. Navigate to `POST /api/v1/ingest/excel` (requires `Auditor` or `SuperAdmin` role).
2. Upload institutional `.xlsx` file containing multi-year sheets (e.g. `Sheet2` with intake, admission, placements, and CET cutoff columns).
3. System verifies structural schema, enforces capacity invariants (`enrolled <= intake`), computes vacancy rates, and commits records to database.

### Workflow 2: Crisis Evaluation & Anomaly Inspection
1. Call `GET /api/v1/institutions/{institution_id}/evaluate`.
2. The engine executes temporal windowing, calculates statistical Z-scores and moving MAD, and computes the Composite Risk Index (CRI):
   - **CRITICAL** ($\text{CRI} \ge 0.70$): Immediate governing board intervention required.
   - **HIGH** ($\text{CRI} \ge 0.50$): Acute operational threat detected.
   - **MEDIUM** ($\text{CRI} \ge 0.30$): Emerging warning signs.
   - **LOW** ($\text{CRI} < 0.30$): Normal baseline operations.
3. Review `anomalies_detected` and `recommended_mitigations`.

### Workflow 3: Forward Policy Simulation
1. Call `POST /api/v1/institutions/{institution_id}/simulate`.
2. Provide hypothetical policy adjustments:
   ```json
   {
     "years_forward": 3,
     "intervention_effects": {
       "placement_boost": 6.0,
       "vacancy_rate_reduction": 0.08
     }
   }
   ```
3. Inspect `status_quo_trajectory` vs. `intervention_trajectory` and `risk_reduction_achieved`.

### Workflow 4: Executive Briefing Generation
1. Call `GET /api/v1/institutions/{institution_id}/report`.
2. System extracts immutable `EvidenceToken` instances from the assessment.
3. Generates structured `ExecutiveNarrativeResponse` containing summary, root causes, and prioritized actions with zero numerical hallucinations.

---

## 6. Observability, Monitoring & Logging

### Health Check Endpoint
- **URL**: `GET /health`
- **Output**:
  ```json
  {
    "status": "HEALTHY",
    "service": "AI CRISS API",
    "version": "1.0.0",
    "database": "CONNECTED",
    "layer_boundaries_verified": true
  }
  ```
- **Monitoring Integration**: Configure Prometheus / Uptime Kuma to poll `/health` every 30 seconds.

### Structured Access Logs
Every HTTP request generates an audit record:
```
2026-09-24 23:56:22 [INFO] ai_criss.api: POST /api/v1/auth/login -> 200 (48.12 ms)
2026-09-24 23:56:23 [INFO] ai_criss.api: GET /api/v1/institutions/RYMEC/evaluate -> 200 (14.30 ms)
```

---

## 7. Disaster Recovery & Backup Strategy

### Automated Backup Execution
The SQLite hot-snapshot utility creates ACID-consistent backup copies with built-in `PRAGMA integrity_check`:
```bash
# Run backup utility via Python
python -c "from src.db.backup import create_backup; print(create_backup())"

# Or trigger via authenticated API (SuperAdmin only)
curl -X POST "http://localhost:8000/api/v1/ops/backup" \
  -H "Authorization: Bearer <SUPERADMIN_TOKEN>"
```

### Backup Rotation Policy
- **Frequency**: Daily at 02:00 UTC.
- **Retention**: 30 daily snapshots, 12 monthly snapshots.
- **Storage Location**: Stored in `./backups/` directory (mounted to persistent volume or S3 bucket in cloud deployments).

### Restoration Procedure
```bash
python -c "from src.db.backup import restore_backup; print(restore_backup('backups/ai_criss_backup_20260924_200000.db'))"
```
