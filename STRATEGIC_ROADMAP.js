const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat, TabStopType, TabStopPosition,
} = require("docx");

// ── Colours ───────────────────────────────────────────────────────────
const NAVY   = "1B2A4A";
const TEAL   = "0D7C66";
const LIGHT  = "EBF5FB";
const MED    = "D5E8F0";
const GREY   = "F5F5F5";
const WHITE  = "FFFFFF";
const BORDER_CLR = "CCCCCC";
const border = { style: BorderStyle.SINGLE, size: 1, color: BORDER_CLR };
const borders = { top: border, bottom: border, left: border, right: border };
const noBorders = {
  top: { style: BorderStyle.NONE, size: 0 },
  bottom: { style: BorderStyle.NONE, size: 0 },
  left: { style: BorderStyle.NONE, size: 0 },
  right: { style: BorderStyle.NONE, size: 0 },
};

// ── Helpers ───────────────────────────────────────────────────────────
const SP = (before = 0, after = 0) => ({ before, after });
const heading = (text, level = HeadingLevel.HEADING_1) =>
  new Paragraph({ heading: level, children: [new TextRun(text)] });

const body = (text, opts = {}) =>
  new Paragraph({
    spacing: SP(60, 60),
    ...opts,
    children: [new TextRun({ text, size: 22, font: "Arial", ...opts.run })],
  });

const boldBody = (label, text) =>
  new Paragraph({
    spacing: SP(60, 60),
    children: [
      new TextRun({ text: label, size: 22, font: "Arial", bold: true }),
      new TextRun({ text, size: 22, font: "Arial" }),
    ],
  });

const cell = (text, opts = {}) =>
  new TableCell({
    borders,
    width: opts.width ? { size: opts.width, type: WidthType.DXA } : undefined,
    shading: opts.shading ? { fill: opts.shading, type: ShadingType.CLEAR } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    verticalAlign: "center",
    children: [
      new Paragraph({
        alignment: opts.align || AlignmentType.LEFT,
        children: [new TextRun({ text, size: 20, font: "Arial", bold: !!opts.bold, color: opts.color })],
      }),
    ],
  });

const headerCell = (text, width) => cell(text, { width, shading: NAVY, bold: true, color: WHITE });

const statusBadge = (status) => {
  const map = {
    "SHIPPED": { fill: "D4EDDA", color: "155724" },
    "IN PROGRESS": { fill: "FFF3CD", color: "856404" },
    "PLANNED": { fill: "D6EAF8", color: "1B4F72" },
    "RESEARCH": { fill: "F5EEF8", color: "6C3483" },
  };
  const s = map[status] || map["PLANNED"];
  return cell(status, { shading: s.fill, color: s.color, bold: true, align: AlignmentType.CENTER });
};

const spacer = () => new Paragraph({ spacing: SP(0, 120), children: [] });

// ── Data ──────────────────────────────────────────────────────────────
const capabilities = [
  { name: "Multi-tier resume extraction (PDF, DOCX, HTML-as-DOCX)", status: "SHIPPED", phase: "Core" },
  { name: "LLM-powered skill extraction with depth scoring (1-5)", status: "SHIPPED", phase: "Core" },
  { name: "Evidence-based capability assessment engine", status: "SHIPPED", phase: "Core" },
  { name: "Risk engine: 8+ flag types (gaps, overlaps, hopping, stale skills, inflation)", status: "SHIPPED", phase: "Core" },
  { name: "Month-level date precision with interval merging", status: "SHIPPED", phase: "Core" },
  { name: "Morphological stemming for recency detection", status: "SHIPPED", phase: "Core" },
  { name: "Business/strategy skill taxonomy (70+ transferability pairs)", status: "SHIPPED", phase: "Core" },
  { name: "Confidence scoring with multi-factor weighting", status: "SHIPPED", phase: "Intelligence" },
  { name: "Score explainability (human-readable drivers)", status: "SHIPPED", phase: "Intelligence" },
  { name: "Bias detection via four-fifths (80%) EEOC rule", status: "SHIPPED", phase: "Compliance" },
  { name: "Intelligence Brief PDF generation (per-candidate)", status: "SHIPPED", phase: "Output" },
  { name: "Batch Analysis Brief PDF (multi-candidate comparison)", status: "SHIPPED", phase: "Output" },
  { name: "Interview question generation (skill verification + gap probing)", status: "SHIPPED", phase: "Output" },
  { name: "ATS webhook integration (Greenhouse, Lever, Ashby, Workday)", status: "SHIPPED", phase: "Integration" },
  { name: "Multi-tenant architecture with company isolation", status: "SHIPPED", phase: "Platform" },
  { name: "JWT auth with role-based access (super_admin, admin, recruiter)", status: "SHIPPED", phase: "Platform" },
  { name: "Rate limiting + security headers middleware", status: "SHIPPED", phase: "Platform" },
  { name: "Audit logging for compliance trail", status: "SHIPPED", phase: "Platform" },
];

const competitors = [
  { name: "Eightfold AI", strength: "800M+ profile graph, skills-based matching, agentic AI", gap: "Black-box scoring, no evidence transparency" },
  { name: "HireVue", strength: "Multi-penalty optimisation, 16K features, open-source bias audit", gap: "Assessment-focused, no resume depth analysis" },
  { name: "Findem", strength: "3D data with Success Signals, cross-source validation", gap: "Sourcing-focused, limited risk detection" },
  { name: "Pymetrics/Harver", strength: "Neuroscience-based assessments, game-based evaluation", gap: "Pre-screen only, no document intelligence" },
  { name: "SeekOut", strength: "Diversity analytics, talent pool mapping", gap: "Search/sourcing tool, no decision intelligence" },
];

const roadmap = [
  { item: "O*NET / ESCO API integration for skill ontology enrichment", status: "PLANNED", quarter: "Q2 2026", impact: "High" },
  { item: "SHAP/LIME-style per-score explainability (visual feature importance)", status: "PLANNED", quarter: "Q2 2026", impact: "High" },
  { item: "Real-time skill graph with adjacency + decay curves", status: "PLANNED", quarter: "Q3 2026", impact: "High" },
  { item: "Unified ATS API: bidirectional sync with stage-write-back", status: "PLANNED", quarter: "Q2 2026", impact: "Critical" },
  { item: "Candidate self-service portal (dispute flags, add context)", status: "PLANNED", quarter: "Q3 2026", impact: "Medium" },
  { item: "Annual bias audit report generator (NYC LL144 / California SB-1162)", status: "PLANNED", quarter: "Q2 2026", impact: "Critical" },
  { item: "Team calibration dashboard (score distribution + inter-rater alignment)", status: "PLANNED", quarter: "Q3 2026", impact: "Medium" },
  { item: "Custom scoring rubric builder (per-company, per-role)", status: "PLANNED", quarter: "Q3 2026", impact: "High" },
  { item: "Slack/Teams integration for real-time recruiter notifications", status: "PLANNED", quarter: "Q4 2026", impact: "Medium" },
  { item: "Multi-language resume support (ESCO multilingual taxonomy)", status: "RESEARCH", quarter: "Q4 2026", impact: "Medium" },
  { item: "Video interview transcript analysis (text-only, no facial/voice)", status: "RESEARCH", quarter: "2027", impact: "High" },
];

// ── Build document ────────────────────────────────────────────────────
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: TEAL },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [
    // ── Cover page ────────────────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: [
        spacer(), spacer(), spacer(), spacer(), spacer(),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: SP(0, 120),
          children: [new TextRun({ text: "VETLAYER", size: 72, bold: true, font: "Arial", color: NAVY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: SP(0, 80),
          children: [new TextRun({ text: "Recruiter Decision Intelligence Platform", size: 32, font: "Arial", color: TEAL })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: TEAL, space: 1 } },
          spacing: SP(200, 200),
          children: [],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: SP(200, 60),
          children: [new TextRun({ text: "Strategic Roadmap & Competitive Position", size: 28, font: "Arial", color: NAVY })],
        }),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: SP(60, 60),
          children: [new TextRun({ text: "March 2026  |  Confidential", size: 22, font: "Arial", color: "666666" })],
        }),
        spacer(), spacer(), spacer(), spacer(), spacer(), spacer(),
        new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "Version 1.0", size: 20, font: "Arial", color: "999999" })],
        }),
      ],
    },

    // ── Executive summary ─────────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            children: [
              new TextRun({ text: "VetLayer Strategic Roadmap", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ text: "\tConfidential", size: 18, font: "Arial", color: "999999" }),
            ],
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: TEAL, space: 4 } },
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "Page ", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial", color: "999999" }),
            ],
          })],
        }),
      },
      children: [
        heading("Executive Summary"),
        body("VetLayer is a recruiter decision intelligence platform that transforms how organisations evaluate talent. Unlike traditional ATS add-ons that rely on keyword matching, VetLayer analyses candidate evidence at depth \u2014 verifying not just whether a skill is mentioned, but how deeply it was applied, for how long, and with what outcomes."),
        spacer(),
        body("The platform combines four proprietary engines: a Capability Assessment Engine that scores skill depth on a 1\u20135 scale against job requirements, a Risk Detection Engine that flags employment gaps, overlapping dates, skill inflation, and career pattern anomalies, a Confidence Scoring system that weights analysis reliability based on evidence quality, and a Bias Detection module implementing EEOC four-fifths rule analysis for adverse impact monitoring."),
        spacer(),
        body("VetLayer supports both technical and business roles equally, with a business skill taxonomy covering 70+ transferability mappings across client experience, strategy, operations, marketing, finance, and HR domains. The ATS integration layer supports Greenhouse, Lever, Ashby, and Workday out of the box, with auto-analysis triggers on candidate application events."),
        spacer(),

        // ── Key metrics box ──────────────────────────────────────
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2340, 2340, 2340, 2340],
          rows: [
            new TableRow({
              children: [
                cell("18 Capabilities Shipped", { width: 2340, shading: LIGHT, bold: true, align: AlignmentType.CENTER }),
                cell("4 ATS Integrations", { width: 2340, shading: LIGHT, bold: true, align: AlignmentType.CENTER }),
                cell("8+ Risk Flag Types", { width: 2340, shading: LIGHT, bold: true, align: AlignmentType.CENTER }),
                cell("70+ Skill Mappings", { width: 2340, shading: LIGHT, bold: true, align: AlignmentType.CENTER }),
              ],
            }),
          ],
        }),
        spacer(),

        // ── Competitive position ─────────────────────────────────
        heading("Competitive Landscape", HeadingLevel.HEADING_1),
        body("The talent intelligence market is crowded, but most players occupy narrow niches. VetLayer is differentiated by combining evidence-based depth assessment with risk detection and compliance tooling in a single platform. No competitor offers all three."),
        spacer(),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [1800, 3200, 4360],
          rows: [
            new TableRow({
              children: [
                headerCell("Competitor", 1800),
                headerCell("Key Strength", 3200),
                headerCell("VetLayer Advantage", 4360),
              ],
            }),
            ...competitors.map((c, i) =>
              new TableRow({
                children: [
                  cell(c.name, { width: 1800, bold: true, shading: i % 2 === 0 ? GREY : WHITE }),
                  cell(c.strength, { width: 3200, shading: i % 2 === 0 ? GREY : WHITE }),
                  cell(c.gap + " \u2014 VetLayer fills this with transparent, evidence-backed scoring.", { width: 4360, shading: i % 2 === 0 ? GREY : WHITE }),
                ],
              })
            ),
          ],
        }),
        spacer(),
        body("VetLayer\u2019s core moat is evidence transparency. While Eightfold and HireVue optimise for prediction accuracy with opaque models, VetLayer shows recruiters exactly why a score was assigned \u2014 which evidence supported it, what gaps exist, and what questions to ask. This makes VetLayer uniquely defensible in regulated industries (financial services, healthcare, government) where algorithmic transparency is legally required."),
      ],
    },

    // ── Shipped capabilities ──────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            children: [
              new TextRun({ text: "VetLayer Strategic Roadmap", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ text: "\tConfidential", size: 18, font: "Arial", color: "999999" }),
            ],
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: TEAL, space: 4 } },
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "Page ", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial", color: "999999" }),
            ],
          })],
        }),
      },
      children: [
        heading("Shipped Capabilities"),
        body("The following capabilities are fully implemented and production-ready as of March 2026."),
        spacer(),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [5500, 1600, 2260],
          rows: [
            new TableRow({
              children: [
                headerCell("Capability", 5500),
                headerCell("Status", 1600),
                headerCell("Domain", 2260),
              ],
            }),
            ...capabilities.map((c, i) =>
              new TableRow({
                children: [
                  cell(c.name, { width: 5500, shading: i % 2 === 0 ? GREY : WHITE }),
                  statusBadge(c.status),
                  cell(c.phase, { width: 2260, shading: i % 2 === 0 ? GREY : WHITE, align: AlignmentType.CENTER }),
                ],
              })
            ),
          ],
        }),

        spacer(),
        new Paragraph({ children: [new PageBreak()] }),

        // ── Architecture deep dive ───────────────────────────────
        heading("Architecture Highlights", HeadingLevel.HEADING_1),

        heading("Evidence-Based Depth Scoring", HeadingLevel.HEADING_2),
        body("Unlike keyword-matching systems, VetLayer\u2019s Capability Assessment Engine evaluates skill depth on a 1\u20135 scale by examining concrete evidence: project descriptions, technologies used, team sizes, outcomes achieved. Each assessment includes a confidence score derived from evidence count, description specificity, and experience recency."),
        spacer(),

        heading("Risk Detection Engine", HeadingLevel.HEADING_2),
        body("The risk engine runs 8+ independent analysers across each candidate profile. Employment gap detection uses month-level date precision with configurable thresholds. Overlapping employment detection compares all experience pairs (not just adjacent ones) and flags overlaps of 2+ months. Job hopping analysis calculates median tenure with severity scaling. Skill inflation detection cross-references claimed depth against verifiable evidence."),
        spacer(),

        heading("Business Role Intelligence", HeadingLevel.HEADING_2),
        body("VetLayer\u2019s skill taxonomy extends beyond technical skills to cover business, strategy, and operations domains. The transferability engine maps 70+ business skill pairs (e.g., Client Experience \u2194 Customer Success, Strategic Planning \u2194 Business Strategy, Change Management \u2194 Transformation Management). Morphological stemming handles word variants (management, manager, managing) to prevent false-positive recency flags on business skills that appear across role titles in various forms."),
        spacer(),

        heading("Compliance & Fairness", HeadingLevel.HEADING_2),
        body("The bias detection module implements the EEOC Uniform Guidelines four-fifths (80%) rule for adverse impact analysis. It monitors selection rates across experience-based cohorts (proxy for age), education levels, and score clustering patterns. VetLayer generates actionable warnings when disparate impact thresholds are breached, supporting NYC Local Law 144 and California SB-1162 compliance."),
        spacer(),

        heading("ATS Integration Layer", HeadingLevel.HEADING_2),
        body("The ATS integration layer provides normalised data objects across Greenhouse, Lever, Ashby, and Workday. Each provider has a dedicated parser that maps proprietary webhook payloads into VetLayer\u2019s unified schema (NormalisedCandidate, NormalisedApplication, NormalisedJob). HMAC-SHA256 signature verification ensures webhook authenticity. Auto-analysis triggers fire on application creation and screening-stage transitions."),
      ],
    },

    // ── Roadmap ───────────────────────────────────────────────────
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            children: [
              new TextRun({ text: "VetLayer Strategic Roadmap", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ text: "\tConfidential", size: 18, font: "Arial", color: "999999" }),
            ],
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: TEAL, space: 4 } },
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "Page ", size: 18, font: "Arial", color: "999999" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 18, font: "Arial", color: "999999" }),
            ],
          })],
        }),
      },
      children: [
        heading("Product Roadmap 2026\u20132027"),
        body("The following items represent VetLayer\u2019s planned development trajectory, prioritised by customer impact and competitive differentiation."),
        spacer(),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [4200, 1500, 1360, 2300],
          rows: [
            new TableRow({
              children: [
                headerCell("Feature", 4200),
                headerCell("Status", 1500),
                headerCell("Target", 1360),
                headerCell("Impact", 2300),
              ],
            }),
            ...roadmap.map((r, i) =>
              new TableRow({
                children: [
                  cell(r.item, { width: 4200, shading: i % 2 === 0 ? GREY : WHITE }),
                  statusBadge(r.status),
                  cell(r.quarter, { width: 1360, shading: i % 2 === 0 ? GREY : WHITE, align: AlignmentType.CENTER }),
                  cell(r.impact, { width: 2300, shading: i % 2 === 0 ? GREY : WHITE, align: AlignmentType.CENTER, bold: r.impact === "Critical" }),
                ],
              })
            ),
          ],
        }),

        spacer(),
        heading("Strategic Priorities", HeadingLevel.HEADING_2),

        heading("Priority 1: Compliance Infrastructure (Q2 2026)", HeadingLevel.HEADING_3),
        body("NYC Local Law 144 requires annual bias audits for automated employment decision tools. California SB-1162 mandates proactive testing with 4-year record retention. VetLayer will ship an automated audit report generator that produces compliant documentation from existing analysis data. This is table stakes for enterprise sales."),
        spacer(),

        heading("Priority 2: ATS Bidirectional Sync (Q2 2026)", HeadingLevel.HEADING_3),
        body("Current ATS integration is inbound-only (webhooks). Bidirectional sync will allow VetLayer to write analysis results, risk flags, and interview questions directly back into the ATS as structured notes or scorecard fields. This eliminates recruiter context-switching and makes VetLayer invisible infrastructure rather than a separate tool."),
        spacer(),

        heading("Priority 3: Skill Ontology Enrichment (Q2\u2013Q3 2026)", HeadingLevel.HEADING_3),
        body("Integrating O*NET (900+ occupations, 35,000+ skill relationships) and ESCO (13,000+ skills, multilingual) will give VetLayer\u2019s skill engine a structured ontology backbone. This enables automatic skill adjacency discovery, standardised occupational mapping, and multilingual resume support for global hiring."),
        spacer(),

        heading("Priority 4: Explainability & Trust (Q2\u2013Q3 2026)", HeadingLevel.HEADING_3),
        body("SHAP/LIME-style feature importance visualisation for every score component. Recruiters will see exactly which factors (specific evidence, experience duration, skill depth) drove the score up or down, with interactive drill-down. This builds recruiter trust and satisfies upcoming EU AI Act transparency requirements for high-risk AI systems in employment."),
        spacer(),

        // ── Closing ──────────────────────────────────────────────
        new Paragraph({ children: [new PageBreak()] }),
        heading("Conclusion"),
        body("VetLayer occupies a unique position in the talent intelligence market: the only platform combining evidence-based depth assessment, risk detection, compliance tooling, and score explainability in a single product. While competitors optimise for either prediction accuracy (Eightfold, HireVue) or sourcing efficiency (Findem, SeekOut), VetLayer optimises for recruiter decision quality \u2014 giving hiring teams the confidence to make defensible, evidence-backed talent decisions."),
        spacer(),
        body("The 2026\u20132027 roadmap focuses on three vectors: compliance readiness for regulatory requirements that are already in effect, ATS integration depth that makes VetLayer invisible infrastructure, and ontology enrichment that keeps the skill engine ahead of the market. With 18 capabilities shipped and a clear path to enterprise-grade compliance, VetLayer is positioned to become the standard for recruiter decision intelligence."),
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync("/sessions/upbeat-serene-ptolemy/mnt/vetlayer/VetLayer_Strategic_Roadmap.docx", buffer);
  console.log("Strategic Roadmap generated successfully.");
});
