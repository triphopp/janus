"""Static route-contract tests for the evidence frontend workflow.

The frontend currently has no JS test runner, so these checks keep the primary
EvidencePanel workflow aligned with backend run-scoped API routes.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API_TS = ROOT / "web" / "frontend" / "src" / "api.ts"
PANEL_TSX = ROOT / "web" / "frontend" / "src" / "components" / "EvidencePanel.tsx"


def test_frontend_investigate_uses_run_scoped_route():
    src = API_TS.read_text()
    assert "/api/evidence/run\"" not in src
    assert "/api/evidence/run'," not in src
    assert "/api/evidence/runs/${encodeURIComponent(req.run_id)}/cases/" in src
    assert "/investigate" in src


def test_frontend_status_polling_uses_run_id_and_case_id():
    api_src = API_TS.read_text()
    panel_src = PANEL_TSX.read_text()

    assert "/api/evidence/cases/${encodeURIComponent(caseId)}/status" not in api_src
    assert "evidenceCaseStatus: (runId: string, caseId: string)" in api_src
    assert "dashboardApi.evidenceCaseStatus(runId, outlier.case_id)" in panel_src
