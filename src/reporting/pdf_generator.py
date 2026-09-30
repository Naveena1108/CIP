"""
Binary PDF Generation Engine for AI CRISS.
Generates deterministic, audit-grade executive crisis dossiers and reports
using ReportLab Platypus.

Ensures strict formatting, mathematical groundedness, and disclosure compliance.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.engine.evidence import InstitutionalDossier
from src.engine.llm_reasoner import ExecutiveNarrativeResponse
from src.engine.predictor import TrajectoryPoint


def _get_risk_palette(risk_level: str):
    """Return background and border color for risk badges."""
    rl = risk_level.upper()
    if "CRITICAL" in rl or "HIGH" in rl:
        return colors.HexColor("#991B1B"), colors.HexColor("#DC2626"), colors.HexColor("#FEF2F2")
    elif "ELEVATED" in rl or "MEDIUM" in rl or "WARNING" in rl:
        return colors.HexColor("#B45309"), colors.HexColor("#D97706"), colors.HexColor("#FFFBEB")
    else:
        return colors.HexColor("#065F46"), colors.HexColor("#059669"), colors.HexColor("#ECFDF5")


def generate_crisis_pdf(
    dossier: InstitutionalDossier,
    narrative: ExecutiveNarrativeResponse,
    trajectory: Optional[List[TrajectoryPoint]] = None,
) -> bytes:
    """
    Render an institutional crisis dossier into an audit-grade binary PDF.

    Parameters
    ----------
    dossier : InstitutionalDossier
        Pre-computed evidence dossier containing mathematical invariants and tokens.
    narrative : ExecutiveNarrativeResponse
        Synthesized executive narrative from LLM or deterministic fallback.
    trajectory : Optional[List[TrajectoryPoint]]
        Autoregressive multi-year forward projections.

    Returns
    -------
    bytes
        Raw binary PDF document content starting with b"%PDF".
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748B"),
    )
    heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1E3A8A"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )
    bullet_style = ParagraphStyle(
        "BulletItem",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3,
    )
    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1,  # Center
    )
    table_cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )
    table_cell_center = ParagraphStyle(
        "TableCellCenter",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
        alignment=1,
    )
    disclosure_style = ParagraphStyle(
        "DisclosureText",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#475569"),
    )

    elements = []

    # 1. Header & Institutional Metadata
    header_data = [
        [
            Paragraph("<b>CIP</b> — Crisis Intelligence Platform Report", title_style),
            Paragraph(
                f"<b>ID:</b> {dossier.institution_id}<br/>"
                f"<b>Generated:</b> {dossier.assessment_timestamp.strftime('%Y-%m-%d %H:%M UTC') if hasattr(dossier.assessment_timestamp, 'strftime') else str(dossier.assessment_timestamp)}",
                subtitle_style,
            ),
        ]
    ]
    header_table = Table(header_data, colWidths=[380, 160])
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(header_table)
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1E3A8A"), spaceAfter=8))

    # 2. Key Metrics & Risk Assessment Banner
    badge_bg, badge_border, banner_tint = _get_risk_palette(dossier.risk_level)

    cri_display = f"{dossier.composite_risk_index:.3f}"
    kpi_data = [
        [
            Paragraph(f"<font size=8 color='#64748B'>COMPOSITE RISK INDEX (CRI)</font><br/><b><font size=20 color='#0F172A'>{cri_display}</font></b><br/><font size=7 color='#64748B'>Scale: 0.000 (Nominal) - 1.000 (Crisis)</font>", body_style),
            Paragraph(f"<font size=8 color='#64748B'>RISK LEVEL</font><br/><b><font size=14 color='{badge_bg.hexval()}'>{dossier.risk_level.upper()}</font></b><br/><font size=7 color='#64748B'>Status: Verified Deterministic</font>", body_style),
            Paragraph(f"<font size=8 color='#64748B'>PRIMARY THREAT DRIVER</font><br/><b><font size=10 color='#0F172A'>{dossier.primary_threat}</font></b><br/><font size=7 color='#64748B'>Anomalies Detected: {dossier.total_anomalies}</font>", body_style),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[180, 160, 200])
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), banner_tint),
                ("BOX", (0, 0), (-1, -1), 1.0, badge_border),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements.append(kpi_table)
    elements.append(Spacer(1, 10))

    # 3. Executive Intelligence Synthesis
    elements.append(Paragraph("1. Executive Intelligence Synthesis", heading_style))
    elements.append(Paragraph(narrative.executive_summary, body_style))
    elements.append(Spacer(1, 6))

    # Root Causes & Actions (2 columns)
    root_cause_paragraphs = [
        Paragraph(f"• {rc}", bullet_style) for rc in narrative.root_causes
    ] if narrative.root_causes else [Paragraph("• No dominant structural root causes identified.", bullet_style)]

    action_paragraphs = [
        Paragraph(f"• {act}", bullet_style) for act in narrative.prioritized_actions
    ] if narrative.prioritized_actions else [Paragraph("• Maintain standard governance and monitoring.", bullet_style)]

    split_content = [
        [
            Paragraph("<b>Identified Root Causes & Vulnerabilities:</b>", body_style),
            Paragraph("<b>Prioritized Strategic Interventions:</b>", body_style),
        ],
        [
            root_cause_paragraphs,
            action_paragraphs,
        ]
    ]
    split_table = Table(split_content, colWidths=[265, 275])
    split_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(split_table)
    elements.append(Spacer(1, 10))

    # 4. Mathematical Evidence Tokens & Anomaly Audit
    elements.append(Paragraph(f"2. Statistical Anomaly Audit ({dossier.total_anomalies} Tokens)", heading_style))

    if dossier.evidence_tokens:
        token_rows = [
            [
                Paragraph("<b>Signal</b>", table_header_style),
                Paragraph("<b>Metric</b>", table_header_style),
                Paragraph("<b>AY</b>", table_header_style),
                Paragraph("<b>Observed</b>", table_header_style),
                Paragraph("<b>Baseline</b>", table_header_style),
                Paragraph("<b>Z-Score</b>", table_header_style),
                Paragraph("<b>Severity</b>", table_header_style),
            ]
        ]
        for t in dossier.evidence_tokens:
            sign = "+" if t.deviation_zscore > 0 else ""
            token_rows.append(
                [
                    Paragraph(t.signal_name, table_cell_style),
                    Paragraph(t.metric_name, table_cell_style),
                    Paragraph(str(t.academic_year), table_cell_center),
                    Paragraph(f"{t.observed_value:.1f}", table_cell_center),
                    Paragraph(f"{t.baseline_value:.1f}", table_cell_center),
                    Paragraph(f"{sign}{t.deviation_zscore:.2f}", table_cell_center),
                    Paragraph(t.severity, table_cell_center),
                ]
            )

        token_table = Table(token_rows, colWidths=[90, 110, 40, 65, 65, 70, 100])
        token_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ]
            )
        )
        elements.append(token_table)
    else:
        empty_note = [
            [Paragraph("<i>No statistical anomalies detected. All observed institutional indicators are within historical tolerance thresholds.</i>", body_style)]
        ]
        empty_table = Table(empty_note, colWidths=[540])
        empty_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("PADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(empty_table)

    elements.append(Spacer(1, 10))

    # 5. Autoregressive Forward Trajectory
    if trajectory:
        elements.append(Paragraph("3. Multi-Year Autoregressive Trajectory Projections", heading_style))
        traj_rows = [
            [
                Paragraph("<b>Projection Offset</b>", table_header_style),
                Paragraph("<b>Scenario</b>", table_header_style),
                Paragraph("<b>Projected CRI</b>", table_header_style),
                Paragraph("<b>95% Confidence Band</b>", table_header_style),
            ]
        ]
        for p in trajectory:
            band_str = f"[{p.confidence_band_low:.3f} — {p.confidence_band_high:.3f}]"
            traj_rows.append(
                [
                    Paragraph(f"+{p.year_offset} Year(s)", table_cell_center),
                    Paragraph(p.scenario, table_cell_style),
                    Paragraph(f"<b>{p.projected_cri:.3f}</b>", table_cell_center),
                    Paragraph(band_str, table_cell_center),
                ]
            )

        traj_table = Table(traj_rows, colWidths=[110, 150, 110, 170])
        traj_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ]
            )
        )
        elements.append(traj_table)
        elements.append(Spacer(1, 10))

    # 6. Audit Provenance & Verification Disclosure
    elements.append(KeepTogether([
        Paragraph("4. Audit Provenance & Mathematical Invariant Guarantee", heading_style),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceAfter=4),
        Paragraph(
            f"<b>Data Sources / Provenance Chain:</b> {', '.join(dossier.provenance_chain) if dossier.provenance_chain else 'ACID_STORE'}<br/>"
            f"<b>Signal Verification:</b> Admissions: {'Verified' if dossier.signal_coverage.get('admissions') else 'Nominal/Absent'}; "
            f"Placements: {'Verified' if dossier.signal_coverage.get('placements') else 'Nominal/Absent'}; "
            f"CET Rankings: {'Verified' if dossier.signal_coverage.get('cet_ranking') else 'Nominal/Absent'}<br/>"
            "<b>Mathematical Invariant Disclosure:</b> The Composite Risk Index (CRI), anomaly Z-scores, and multi-year trajectory projections "
            "are calculated via deterministic mathematical algorithms. The AI reasoning engine interprets evidence without altering numerical data.",
            disclosure_style,
        )
    ]))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
