"""
Validation Test Suite for CIP Phase 6: Complete Product-Level UI Transformation.

Verifies:
1. Public Branding: CIP / Crisis Intelligence Platform (no user-facing AI-CRISS or Cockpit branding).
2. UX Principle: Removal of neon/glow/glassmorphism, palette selectors, synthetic test scenario selectors, and RYMEC labels from normal UX.
3. Themes Only: Light, Dark, and System Default supported.
4. Navigation: 10 dedicated workflows (Overview, Investigations, Insights, Risks, Predictions, What-If Analysis, Evidence, Data, Institutional Memory, Profile) with entity-type and permission adaptation.
5. Overview: Answers the 5 executive questions (What is happening? What changed? What needs attention? What evidence supports it? What could happen next?).
6. Insights / Risks: Communicates change, magnitude, period, significance, evidence, severity, and next investigation area, plus the 5-stage Risk Progression Ladder.
7. Predictions, What-If Analysis, Data, and Profile workflow completeness.
"""

from fastapi.testclient import TestClient
from src.api.main import app


client = TestClient(app)


def test_phase6_public_branding_and_removal_of_legacy_cockpit():
    """Verify CIP / Crisis Intelligence Platform public branding and removal of AI-CRISS / Cockpit."""
    root_res = client.get("/")
    assert root_res.status_code == 200
    assert "CIP — Crisis Intelligence Platform" in root_res.json()["message"]
    assert "AI CRISS" not in root_res.json()["message"]

    dash_res = client.get("/dashboard/")
    assert dash_res.status_code == 200
    html = dash_res.text

    # Public CIP branding present
    assert "<title>CIP — Crisis Intelligence Platform</title>" in html
    assert "Crisis Intelligence Platform" in html

    # Legacy user-facing branding & cockpit terminology removed
    assert "AI-CRISS" not in html
    assert "AI CRISS" not in html
    assert "Institutional Crisis Intelligence Cockpit" not in html

    # Neon, glassmorphism, synthetic scenario selector, and dataset-specific RYMEC labels removed
    assert "glass-panel" not in html
    assert "backdrop-filter: blur" not in html
    assert "glow-red" not in html
    assert "glow-green" not in html
    assert 'id="scenarioSelect"' not in html
    assert "Run Scenario" not in html
    assert "RYMEC" not in html


def test_phase6_themes_light_dark_system_default_only():
    """Verify only Light, Dark, and System Default themes are exposed."""
    dash_res = client.get("/dashboard/")
    assert dash_res.status_code == 200
    html = dash_res.text

    assert 'id="themeSelector"' in html
    assert "Theme: System Default" in html
    assert "Theme: Light" in html
    assert "Theme: Dark" in html
    assert 'html[data-theme="light"]' in html
    assert 'html[data-theme="dark"]' in html
    assert "prefers-color-scheme: dark" in html


def test_phase6_ten_user_facing_navigation_workflows_and_adaptation():
    """Verify all 10 user-facing workflows and entity/permission adaptation functions exist."""
    dash_res = client.get("/dashboard/")
    assert dash_res.status_code == 200
    html = dash_res.text

    workflows = [
        ("overview", "Overview"),
        ("investigations", "Investigations"),
        ("insights", "Insights"),
        ("risks", "Risks"),
        ("predictions", "Predictions"),
        ("whatif", "What-If Analysis"),
        ("evidence", "Evidence"),
        ("data", "Data"),
        ("memory", "Institutional Memory"),
        ("profile", "Profile"),
    ]
    for wf_id, wf_label in workflows:
        assert f'id="nav-{wf_id}"' in html
        assert f'id="view-{wf_id}"' in html
        assert wf_label in html

    # Verify navigation adapts to entity type and user permissions
    assert "adaptNavigationToEntityAndPermissions" in html
    assert "navigateWorkflow" in html


def test_phase6_overview_five_questions_and_workflow_sections():
    """Verify Overview answers the 5 core executive questions and all workflow attributes are present."""
    dash_res = client.get("/dashboard/")
    assert dash_res.status_code == 200
    html = dash_res.text

    # Overview 5 questions
    assert "1. What is happening?" in html
    assert "2. What changed?" in html
    assert "3. What needs attention?" in html
    assert "4. What evidence supports it?" in html
    assert "5. What could happen next?" in html
    assert 'id="ovWhatIsHappening"' in html
    assert 'id="ovWhatChanged"' in html
    assert 'id="ovWhatNeedsAttention"' in html
    assert 'id="ovSupportingEvidence"' in html
    assert 'id="ovWhatCouldHappenNext"' in html

    # Insights / Risks 7 attributes
    assert "1. Change:" in html
    assert "2. Magnitude:" in html
    assert "3. Period &amp; Basis:" in html
    assert "4. Significance:" in html
    assert "5. Supporting Evidence:" in html
    assert "6. Next Investigation Area:" in html
    assert "Severity:" in html

    # Predictions 8 attributes
    assert "1. Horizon:" in html
    assert "2. Confidence &amp; Coverage:" in html
    assert "3. Method Actually Used:" in html
    assert "4. Forecast Trajectory Points:" in html
    assert '5. Reason for Change ("Why did the prediction change?"):' in html
    assert "6. Input Signals &amp; Trajectory Drivers:" in html
    assert "7. Historical Evidence &amp; 8. Limitations:" in html

    # What-If Analysis 5 attributes
    assert "Baseline (Year +3 CRI)" in html
    assert "Intervention (Year +3 CRI)" in html
    assert "Estimated Risk Change" in html
    assert "Explanation &amp; Reason for Change:" in html

    # Data workflow 6 attributes
    assert "Add Data" in html
    assert "Detected Format" in html
    assert "Processing Status" in html
    assert "Signals Discovered" in html
    assert "Data Quality Issues" in html
    assert "Unsupported Format &amp; Partial Extraction Policy" in html

    # Profile workflow 4 sections
    assert "Entity Hierarchy" in html
    assert "Institution Profile" in html
    assert "Organization / Network" in html
    assert "User Profile" in html
