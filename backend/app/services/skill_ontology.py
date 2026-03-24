"""
Skill Ontology — The universal knowledge graph for VetLayer.

Replaces the flat _EVIDENCE_ALIASES dict with a proper ontology that:
1. Separates skill identity from evidence matching
2. Handles equivalences (IFRS = International Financial Reporting Standards)
3. Tags skills by domain so role type detection stays decoupled
4. Provides domain-specific proficiency anchors
5. Supports parent/child/sibling relationships for adjacency

Design principles (from ESCO, O*NET, Lightcast, SFIA research):
- Skills have canonical IDs independent of display names
- Each skill belongs to one or more domains
- Proficiency is defined by autonomy + influence + complexity (SFIA model)
- Evidence matching uses variants (for extraction) not the ontology graph (for classification)
"""

import re
import logging
from typing import Dict, List, Set, Optional, Tuple, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Core data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SkillNode:
    """A single skill in the ontology."""
    id: str                                  # Canonical ID (e.g., "ifrs")
    name: str                                # Display name (e.g., "IFRS")
    domain: str                              # Primary domain
    domains: List[str] = field(default_factory=list)  # All applicable domains
    variants: List[str] = field(default_factory=list)  # Text variants for matching
    parent: Optional[str] = None             # Parent skill ID
    children: List[str] = field(default_factory=list)
    siblings: List[str] = field(default_factory=list)  # Related skills at same level
    skill_type: str = "hard"                 # hard | soft | tool | certification
    contextual_phrases: List[str] = field(default_factory=list)  # Phrases implying this skill


@dataclass
class ProficiencyAnchor:
    """Domain-specific behavioral anchor for a proficiency level."""
    level: int          # 0-5
    name: str           # e.g., "Professional"
    description: str    # Universal description
    examples: Dict[str, str] = field(default_factory=dict)  # domain -> example


# ═══════════════════════════════════════════════════════════════════════
# Domains — the top-level classification
# ═══════════════════════════════════════════════════════════════════════

DOMAINS = {
    "technology": "Software engineering, data, DevOps, cloud, AI/ML",
    "finance": "Accounting, financial reporting, treasury, risk, compliance",
    "consulting": "Strategy, transformation, client advisory, change management",
    "operations": "Process improvement, supply chain, logistics, quality",
    "hr": "Talent acquisition, L&D, compensation, employee relations",
    "marketing": "Digital marketing, brand, content, analytics, CRM",
    "sales": "Account management, business development, pipeline, CRM",
    "healthcare": "Clinical, regulatory, pharma, medical devices",
    "legal": "Compliance, contracts, regulatory affairs, IP",
    "leadership": "Team management, strategy, governance, stakeholder engagement",
    "general": "Cross-domain tools and methodologies",
}


# ═══════════════════════════════════════════════════════════════════════
# Universal proficiency scale (SFIA-inspired, domain-neutral)
# ═══════════════════════════════════════════════════════════════════════

PROFICIENCY_SCALE = {
    0: ProficiencyAnchor(
        level=0,
        name="Not Found",
        description="No evidence of this skill anywhere on the resume.",
    ),
    1: ProficiencyAnchor(
        level=1,
        name="Awareness",
        description="Listed or mentioned in passing. No evidence of hands-on application.",
        examples={
            "technology": "Listed Docker in skills section with no project using it.",
            "finance": "Knowledge of IFRS listed under skills with no application context.",
            "operations": "Mentioned lean methodology in summary but no implementation described.",
            "leadership": "Described as 'team player' but no leadership scope evidenced.",
        },
    ),
    2: ProficiencyAnchor(
        level=2,
        name="Practitioner",
        description="Applied in a limited or supporting capacity. Works under guidance.",
        examples={
            "technology": "Used Redis in a hackathon. Completed a React tutorial project.",
            "finance": "Assisted with IFRS reporting as a junior analyst.",
            "operations": "Participated in process improvement workshops.",
            "hr": "Supported recruitment drives as part of a team.",
            "consulting": "Contributed to client presentations as an associate.",
        },
    ),
    3: ProficiencyAnchor(
        level=3,
        name="Professional",
        description="Applied regularly in a professional setting with real responsibility. "
                    "1+ years delivering outcomes independently.",
        examples={
            "technology": "Built REST APIs with FastAPI serving production traffic for 10K+ users.",
            "finance": "Managed IFRS 9 reporting across multiple legal entities. Owned monthly close.",
            "operations": "Led process reengineering that reduced cycle time by 30%.",
            "hr": "Designed and ran performance management cycles for 500+ employees.",
            "consulting": "Led workstreams on $5M+ consulting engagements.",
            "marketing": "Managed $500K annual digital marketing budget with measurable ROI.",
            "sales": "Owned a book of 20+ enterprise accounts with consistent quota attainment.",
            "leadership": "Managed a team of 5-15 people with hiring and performance responsibility.",
        },
    ),
    4: ProficiencyAnchor(
        level=4,
        name="Advanced",
        description="Led strategy, owned outcomes, designed frameworks, mentored others. "
                    "2+ years of deep professional use with demonstrated impact.",
        examples={
            "technology": "Architected microservices migration serving 2M users. Led team Kubernetes adoption.",
            "finance": "Designed IFRS implementation program across 12 entities. Trained 40+ controllers.",
            "operations": "Built operational excellence framework adopted across 3 business units.",
            "consulting": "Led $50M transformation engagement. Designed operating model for Fortune 500 client.",
            "hr": "Built company-wide talent management strategy. Reduced attrition by 25%.",
            "marketing": "Developed brand strategy for product launch generating $10M pipeline.",
            "leadership": "Led cross-functional team of 30+ across multiple geographies.",
        },
    ),
    5: ProficiencyAnchor(
        level=5,
        name="Expert",
        description="Industry-recognized authority. Published thought leadership, shaped standards, "
                    "or built widely adopted frameworks.",
        examples={
            "technology": "Created open source library with 5K+ stars. Published distributed systems paper.",
            "finance": "Led IFRS adoption for a G-SIB. Served on industry working groups.",
            "healthcare": "Designed clinical governance framework adopted across hospital network.",
            "consulting": "Built methodology adopted firm-wide. Recognized thought leader.",
            "leadership": "C-suite executive with P&L responsibility over $100M+ business.",
        },
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# Skill nodes — the actual ontology
# ═══════════════════════════════════════════════════════════════════════

def _build_ontology() -> Dict[str, SkillNode]:
    """Build the complete skill ontology. Returns dict of id -> SkillNode."""
    nodes = {}

    def _add(id: str, name: str, domain: str, variants: list,
             parent: str = None, siblings: list = None,
             domains: list = None, skill_type: str = "hard",
             contextual: list = None, children: list = None):
        nodes[id] = SkillNode(
            id=id, name=name, domain=domain,
            domains=domains or [domain],
            variants=[v.lower() for v in variants],
            parent=parent,
            siblings=siblings or [],
            skill_type=skill_type,
            contextual_phrases=[p.lower() for p in (contextual or [])],
            children=children or [],
        )

    # ── Technology: Languages ─────────────────────────────────────────
    _add("python", "Python", "technology",
         ["python", "python3", "python 3", "py"],
         contextual=["django", "flask", "fastapi", "pandas", "numpy", "pytorch"])
    _add("javascript", "JavaScript", "technology",
         ["javascript", "js", "ecmascript", "es6", "es2015", "vanilla js"])
    _add("typescript", "TypeScript", "technology",
         ["typescript", "ts"])
    _add("java", "Java", "technology",
         ["java", "jvm", "j2ee", "j2se"],
         contextual=["spring", "maven", "gradle", "hibernate"])
    _add("csharp", "C#", "technology",
         ["c#", "csharp", "c sharp", ".net", "dotnet"])
    _add("go", "Go", "technology",
         ["golang", "go lang"])
    _add("rust", "Rust", "technology", ["rust", "cargo"])
    _add("ruby", "Ruby", "technology", ["ruby"],
         contextual=["rails", "ruby on rails"])
    _add("php", "PHP", "technology",
         ["php", "php7", "php8", "php 7", "php 8"],
         contextual=["laravel", "symfony", "wordpress"])
    _add("sql", "SQL", "technology",
         ["sql", "mysql", "postgresql", "postgres", "sqlite", "t-sql", "pl/sql",
          "plsql", "transact-sql", "mariadb"],
         contextual=["database queries", "stored procedures", "query optimization"])
    _add("html", "HTML", "technology",
         ["html", "html5", "html 5", "markup"])
    _add("css", "CSS", "technology",
         ["css", "css3", "css 3", "stylesheets", "tailwind", "bootstrap",
          "styled-components", "emotion"])

    # ── Technology: Frontend Frameworks ────────────────────────────────
    _add("react", "React", "technology",
         ["react", "reactjs", "react.js", "react js"],
         contextual=["jsx", "hooks", "redux", "react native"])
    _add("angular", "Angular", "technology",
         ["angular", "angularjs", "angular.js", "angular 2", "angular 12", "angular 15"])
    _add("vue", "Vue", "technology",
         ["vue", "vuejs", "vue.js", "vue 3", "vuex", "pinia", "nuxt"])
    _add("nextjs", "Next.js", "technology",
         ["next.js", "nextjs", "next js"])
    _add("svelte", "Svelte", "technology",
         ["svelte", "sveltekit", "svelte kit"])

    # ── Technology: Backend Frameworks ─────────────────────────────────
    _add("nodejs", "Node.js", "technology",
         ["node.js", "nodejs", "node js", "node"],
         contextual=["express", "koa", "nestjs", "fastify"])
    _add("django", "Django", "technology", ["django", "django rest framework", "drf"])
    _add("flask", "Flask", "technology", ["flask"])
    _add("fastapi", "FastAPI", "technology", ["fastapi", "fast api"])
    _add("spring_boot", "Spring Boot", "technology",
         ["spring boot", "spring", "spring framework", "spring mvc"])
    _add("dotnet", ".NET", "technology",
         [".net", "dotnet", ".net core", "asp.net", "asp.net core", "entity framework"])
    _add("rails", "Ruby on Rails", "technology",
         ["ruby on rails", "rails", "ror"])
    _add("laravel", "Laravel", "technology", ["laravel"])

    # ── Technology: Databases ─────────────────────────────────────────
    _add("postgresql", "PostgreSQL", "technology",
         ["postgresql", "postgres", "psql"])
    _add("mongodb", "MongoDB", "technology",
         ["mongodb", "mongo", "mongoose"])
    _add("redis", "Redis", "technology",
         ["redis", "redis cache", "elasticache"])
    _add("elasticsearch", "Elasticsearch", "technology",
         ["elasticsearch", "elastic search", "elk stack", "elk", "opensearch"])
    _add("dynamodb", "DynamoDB", "technology",
         ["dynamodb", "dynamo db", "dynamo"])

    # ── Technology: Cloud & DevOps ────────────────────────────────────
    _add("aws", "AWS", "technology",
         ["aws", "amazon web services", "ec2", "s3", "lambda", "ecs", "eks",
          "cloudformation", "sqs", "sns", "rds", "aurora"])
    _add("gcp", "Google Cloud", "technology",
         ["gcp", "google cloud", "google cloud platform", "bigquery", "cloud run",
          "cloud functions", "gke"])
    _add("azure", "Azure", "technology",
         ["azure", "microsoft azure", "azure devops", "azure functions"])
    _add("docker", "Docker", "technology",
         ["docker", "dockerfile", "docker compose", "docker-compose", "containerization"])
    _add("kubernetes", "Kubernetes", "technology",
         ["kubernetes", "k8s", "kubectl", "helm", "helm charts"])
    _add("terraform", "Terraform", "technology",
         ["terraform", "terraform modules", "hcl", "infrastructure as code", "iac"])
    _add("cicd", "CI/CD", "technology",
         ["ci/cd", "ci cd", "cicd", "continuous integration", "continuous deployment",
          "github actions", "gitlab ci", "jenkins", "circleci", "buildkite"])
    _add("linux", "Linux", "technology",
         ["linux", "ubuntu", "centos", "debian", "rhel", "bash", "shell scripting"])
    _add("git", "Git", "technology",
         ["git", "github", "gitlab", "bitbucket", "version control"])

    # ── Technology: Data & ML ─────────────────────────────────────────
    _add("pandas", "Pandas", "technology", ["pandas"])
    _add("tensorflow", "TensorFlow", "technology", ["tensorflow", "tf", "keras"])
    _add("pytorch", "PyTorch", "technology", ["pytorch", "torch"])
    _add("spark", "Apache Spark", "technology",
         ["spark", "pyspark", "apache spark", "spark sql"])
    _add("airflow", "Apache Airflow", "technology",
         ["airflow", "apache airflow", "dag", "data pipelines"])

    # ── Technology: Testing ───────────────────────────────────────────
    _add("testing", "Testing", "technology",
         ["unit testing", "integration testing", "e2e testing", "test automation",
          "jest", "pytest", "mocha", "cypress", "selenium", "playwright", "vitest"],
         contextual=["test coverage", "test driven", "tdd", "bdd"])

    # ── Technology: APIs ──────────────────────────────────────────────
    _add("rest_api", "REST APIs", "technology",
         ["rest api", "rest apis", "restful", "api design", "api development",
          "openapi", "swagger"])
    _add("graphql", "GraphQL", "technology",
         ["graphql", "graph ql", "apollo", "apollo server", "apollo client"])

    # ── Technology: Security ──────────────────────────────────────────
    _add("security", "Security", "technology",
         ["cybersecurity", "application security", "appsec", "infosec",
          "penetration testing", "vulnerability assessment", "owasp",
          "oauth", "jwt", "ssl", "tls", "encryption", "iam"])

    # ── Technology: Mobile ────────────────────────────────────────────
    _add("react_native", "React Native", "technology",
         ["react native", "react-native", "expo"])
    _add("flutter", "Flutter", "technology", ["flutter", "dart"])
    _add("ios", "iOS Development", "technology",
         ["ios", "swift", "swiftui", "uikit", "objective-c", "xcode"])
    _add("android", "Android Development", "technology",
         ["android", "kotlin", "android studio", "jetpack compose"])

    # ── Finance & Accounting ──────────────────────────────────────────
    _add("financial_reporting", "Financial Reporting", "finance",
         ["financial reporting", "financial statements", "financial report",
          "balance sheet", "income statement", "cash flow statement",
          "profit and loss", "p&l", "annual report", "quarterly reporting"],
         contextual=["close process", "financial close", "month-end close",
                     "reporting cycle", "consolidated financials", "statutory reporting"])
    _add("ifrs", "IFRS", "finance",
         ["ifrs", "international financial reporting standards",
          "ifrs 9", "ifrs 15", "ifrs 16", "ifrs 17", "ind as"],
         siblings=["gaap", "accounting_standards"],
         contextual=["international accounting standards", "ias",
                     "expected credit loss", "revenue recognition standard"])
    _add("gaap", "GAAP", "finance",
         ["gaap", "us gaap", "generally accepted accounting principles",
          "asc 606", "asc 842", "asc 326"],
         siblings=["ifrs", "accounting_standards"])
    _add("accounting_standards", "Accounting Standards", "finance",
         ["accounting standards", "accounting principles", "accounting framework",
          "accounting policies", "accounting treatment"],
         children=["ifrs", "gaap"])
    _add("financial_controllership", "Financial Controllership", "finance",
         ["financial controllership", "financial controller", "controllership",
          "finance controller", "group controller"],
         contextual=["financial controls", "internal controls over financial reporting",
                     "icfr", "sox compliance", "financial governance"])
    _add("financial_analysis", "Financial Analysis", "finance",
         ["financial analysis", "financial analyst", "financial modeling",
          "financial model", "dcf", "discounted cash flow", "variance analysis",
          "ratio analysis", "trend analysis"],
         contextual=["ebitda", "roi analysis", "npv", "irr", "sensitivity analysis",
                     "scenario analysis", "financial forecast"])
    _add("financial_planning", "Financial Planning", "finance",
         ["financial planning", "fp&a", "budgeting", "forecasting",
          "budget", "annual operating plan", "long range plan",
          "budgeting and forecasting"],
         contextual=["annual budget", "rolling forecast", "budget vs actual",
                     "variance reporting", "cost center management"])
    _add("risk_management", "Risk Management", "finance",
         ["risk management", "enterprise risk", "risk assessment",
          "risk framework", "risk mitigation", "risk register",
          "credit risk", "market risk", "operational risk"],
         domains=["finance", "operations", "consulting"],
         contextual=["risk appetite", "risk tolerance", "key risk indicators",
                     "risk committee", "risk reporting"])
    _add("compliance", "Compliance", "finance",
         ["compliance", "regulatory compliance", "regulatory reporting",
          "compliance framework", "compliance program", "compliance officer"],
         domains=["finance", "legal", "healthcare"],
         contextual=["regulatory requirements", "compliance monitoring",
                     "audit findings", "remediation", "policy compliance"])
    _add("audit", "Audit", "finance",
         ["audit", "internal audit", "external audit", "audit committee",
          "audit findings", "audit report", "sox audit"],
         contextual=["audit plan", "audit trail", "audit procedures",
                     "material weakness", "significant deficiency"])
    _add("treasury", "Treasury", "finance",
         ["treasury", "treasury management", "cash management",
          "liquidity management", "cash flow management", "bank relationships"],
         contextual=["cash position", "working capital", "debt management",
                     "hedging", "fx management", "interest rate management"])
    _add("taxation", "Taxation", "finance",
         ["taxation", "tax", "tax planning", "tax compliance",
          "transfer pricing", "direct tax", "indirect tax", "gst", "vat"],
         contextual=["tax return", "tax provision", "deferred tax",
                     "tax optimization", "tax advisory"])
    _add("accounting", "Accounting", "finance",
         ["accounting", "general ledger", "accounts payable", "accounts receivable",
          "reconciliation", "journal entries", "chart of accounts",
          "cost accounting", "management accounting"],
         contextual=["month end", "year end", "accruals", "provisions",
                     "intercompany", "consolidation"])
    _add("erp", "ERP Systems", "finance",
         ["erp", "sap", "oracle financials", "oracle ebs", "netsuite",
          "workday financials", "sage", "tally", "quickbooks"],
         domains=["finance", "operations", "general"],
         skill_type="tool")
    _add("strategy_development", "Strategy Development", "finance",
         ["strategy development", "strategic planning", "corporate strategy",
          "business strategy", "strategic initiatives", "strategy execution"],
         domains=["finance", "consulting", "leadership"],
         contextual=["strategic roadmap", "strategy formulation",
                     "competitive strategy", "growth strategy"])
    _add("financial_governance", "Financial Governance", "finance",
         ["financial governance", "corporate governance", "governance framework",
          "board reporting", "governance structure"],
         contextual=["governance committee", "governance policies",
                     "fiduciary responsibility"])

    # ── Operations & Process ──────────────────────────────────────────
    _add("process_improvement", "Process Improvement", "operations",
         ["process improvement", "process optimization", "process reengineering",
          "continuous improvement", "lean", "six sigma", "lean six sigma",
          "kaizen", "operational efficiency"],
         contextual=["making processes lean", "cost savings", "cycle time reduction",
                     "waste elimination", "value stream mapping", "root cause analysis"])
    _add("project_management", "Project Management", "operations",
         ["project management", "project manager", "pmp", "prince2",
          "project planning", "project delivery", "program management"],
         domains=["operations", "technology", "consulting", "general"],
         contextual=["project plan", "milestone", "deliverable", "gantt",
                     "stakeholder reporting", "project governance"])
    _add("operations_management", "Operations Management", "operations",
         ["operations management", "operational excellence", "operations director",
          "operations manager", "business operations"],
         contextual=["operational kpis", "service delivery", "operational metrics",
                     "capacity planning", "demand planning"])
    _add("supply_chain", "Supply Chain Management", "operations",
         ["supply chain", "supply chain management", "scm", "logistics",
          "procurement", "sourcing", "vendor management",
          "inventory management", "warehouse management"],
         contextual=["supplier relationship", "procurement strategy",
                     "inventory optimization", "demand forecasting"])
    _add("data_analysis", "Data Analysis", "operations",
         ["data analysis", "data analytics", "business intelligence",
          "reporting", "dashboards", "data visualization",
          "excel", "power bi", "tableau"],
         domains=["operations", "technology", "finance", "marketing", "general"],
         contextual=["kpi tracking", "metrics analysis", "trend analysis",
                     "data-driven", "analytical insights"])
    _add("quality_management", "Quality Management", "operations",
         ["quality management", "quality assurance", "quality control",
          "iso 9001", "total quality management", "tqm"],
         contextual=["quality standards", "quality metrics", "defect reduction"])

    # ── HR & People ───────────────────────────────────────────────────
    _add("talent_acquisition", "Talent Acquisition", "hr",
         ["talent acquisition", "recruitment", "recruiting", "sourcing",
          "employer branding", "campus hiring", "lateral hiring"])
    _add("performance_management", "Performance Management", "hr",
         ["performance management", "performance review", "performance appraisal",
          "goal setting", "okrs", "kpis for people"],
         contextual=["performance cycle", "pip", "talent review", "calibration"])
    _add("employee_engagement", "Employee Engagement", "hr",
         ["employee engagement", "employee satisfaction", "employee experience",
          "retention", "culture building", "eNPS"],
         contextual=["engagement survey", "pulse survey", "attrition"])
    _add("compensation_benefits", "Compensation & Benefits", "hr",
         ["compensation", "benefits", "total rewards", "salary benchmarking",
          "equity compensation", "incentive design"])
    _add("learning_development", "Learning & Development", "hr",
         ["learning and development", "l&d", "training", "capability building",
          "leadership development", "upskilling"],
         domains=["hr", "leadership"],
         contextual=["training programs", "learning paths", "skill development"])

    # ── Marketing & Sales ─────────────────────────────────────────────
    _add("digital_marketing", "Digital Marketing", "marketing",
         ["digital marketing", "online marketing", "seo", "sem", "ppc",
          "google ads", "facebook ads", "social media marketing",
          "content marketing", "email marketing", "marketing automation"],
         contextual=["campaign performance", "conversion rate", "ctr",
                     "marketing funnel", "lead generation"])
    _add("brand_management", "Brand Management", "marketing",
         ["brand management", "brand strategy", "brand positioning",
          "brand identity", "rebranding"],
         contextual=["brand awareness", "brand equity", "brand guidelines"])
    _add("account_management", "Account Management", "sales",
         ["account management", "key account management", "account manager",
          "client management", "customer relationship management"],
         contextual=["account growth", "account retention", "client portfolio",
                     "revenue growth from accounts", "upselling", "cross-selling"])
    _add("business_development", "Business Development", "sales",
         ["business development", "biz dev", "bd", "new business",
          "pipeline development", "market development"],
         contextual=["new client acquisition", "pipeline building",
                     "partnership development", "market entry"])
    _add("crm", "CRM", "sales",
         ["crm", "salesforce", "hubspot", "dynamics 365", "zoho crm",
          "customer relationship management"],
         skill_type="tool")

    # ── Leadership & Management ───────────────────────────────────────
    _add("team_leadership", "Team Leadership", "leadership",
         ["team leadership", "team management", "people management",
          "managing teams", "team building", "leading teams"],
         domains=["leadership", "general"],
         contextual=["led a team of", "managed a team", "direct reports",
                     "team of", "headcount", "built a team"])
    _add("stakeholder_engagement", "Stakeholder Engagement", "leadership",
         ["stakeholder engagement", "stakeholder management",
          "executive engagement", "senior stakeholder",
          "client engagement", "partner engagement"],
         domains=["leadership", "consulting", "sales"],
         contextual=["c-suite engagement", "board presentation",
                     "executive communication", "influencing without authority"])
    _add("governance", "Governance", "leadership",
         ["governance", "governance framework", "governance model",
          "decision rights", "operating model", "governance structure"],
         domains=["leadership", "finance", "operations"],
         contextual=["intake criteria", "escalation", "prioritization framework",
                     "service standards", "sla", "process governance",
                     "governance committee", "operating cadence"])
    _add("change_management", "Change Management", "leadership",
         ["change management", "organizational change", "transformation",
          "change program", "change readiness"],
         domains=["leadership", "consulting", "hr"],
         contextual=["change adoption", "stakeholder alignment",
                     "communication plan", "resistance management"])

    # ── Client Experience & Consulting ────────────────────────────────
    _add("client_experience_strategy", "Client Experience Strategy", "consulting",
         ["client experience", "customer experience", "cx strategy",
          "experience strategy", "client experience strategy",
          "customer experience strategy"],
         contextual=["client visits", "client engagement model",
                     "experience design", "client journey",
                     "differentiated client experience"])
    _add("experience_design", "Experience Design", "consulting",
         ["experience design", "service design", "design thinking",
          "journey mapping", "experience architecture"],
         contextual=["interactive formats", "narrative-driven",
                     "outcome-focused", "insight-rich experiences"])
    _add("business_acumen", "Business Acumen", "consulting",
         ["business acumen", "commercial acumen", "business model",
          "account economics", "go-to-market", "revenue model"],
         domains=["consulting", "leadership", "sales"],
         contextual=["p&l understanding", "revenue drivers",
                     "business case", "commercial impact"])
    _add("capability_building", "Capability Building", "consulting",
         ["capability building", "capacity building", "capability development",
          "competency building", "building capability"],
         domains=["consulting", "hr", "leadership"],
         contextual=["playbooks", "templates", "coaching",
                     "upskilling teams", "knowledge transfer"])
    _add("operational_excellence", "Operational Excellence", "operations",
         ["operational excellence", "opex", "ops excellence",
          "operational rigor", "operational discipline"],
         domains=["operations", "consulting"],
         contextual=["end-to-end governance", "structured intake",
                     "service standards", "continuous improvement",
                     "post-visit debrief", "readiness reviews"])

    # ── General / Cross-domain ────────────────────────────────────────
    _add("microsoft_office", "Microsoft Office", "general",
         ["microsoft office", "ms office", "excel", "word", "powerpoint",
          "outlook", "office 365", "microsoft 365"],
         skill_type="tool")
    _add("agile", "Agile Methodology", "general",
         ["agile", "scrum", "kanban", "sprint", "agile methodology",
          "safe", "scaled agile"],
         domains=["general", "technology", "operations"],
         contextual=["sprint planning", "retrospective", "backlog",
                     "user stories", "velocity", "standup"])

    return nodes


# ═══════════════════════════════════════════════════════════════════════
# Ontology singleton and query API
# ═══════════════════════════════════════════════════════════════════════

_ONTOLOGY: Optional[Dict[str, SkillNode]] = None


def get_ontology() -> Dict[str, SkillNode]:
    """Get the skill ontology (built once, cached)."""
    global _ONTOLOGY
    if _ONTOLOGY is None:
        _ONTOLOGY = _build_ontology()
        logger.info(f"Skill ontology built: {len(_ONTOLOGY)} skills across "
                    f"{len(set(n.domain for n in _ONTOLOGY.values()))} domains")
    return _ONTOLOGY


def resolve_skill(skill_name: str) -> Optional[SkillNode]:
    """
    Resolve a skill name to its ontology node.
    Tries: exact ID match, then case-insensitive name match, then variant match.
    """
    ontology = get_ontology()
    name_lower = skill_name.lower().strip()

    # 1. Exact ID match
    if name_lower in ontology:
        return ontology[name_lower]

    # 2. Name match (case-insensitive)
    for node in ontology.values():
        if node.name.lower() == name_lower:
            return node

    # 3. Variant match
    for node in ontology.values():
        if name_lower in node.variants:
            return node

    return None


def get_skill_domain(skill_name: str) -> str:
    """Get the primary domain for a skill. Returns 'unknown' if not in ontology."""
    node = resolve_skill(skill_name)
    return node.domain if node else "unknown"


def get_skills_by_domain(domain: str) -> List[SkillNode]:
    """Get all skills belonging to a domain."""
    ontology = get_ontology()
    return [n for n in ontology.values() if domain in (n.domains or [n.domain])]


def get_evidence_variants(skill_name: str) -> List[str]:
    """Get all text variants for evidence matching. Falls back to [skill_name.lower()]."""
    node = resolve_skill(skill_name)
    if node:
        return node.variants
    return [skill_name.lower().strip()]


def get_contextual_phrases(skill_name: str) -> List[str]:
    """Get contextual phrases that imply this skill."""
    node = resolve_skill(skill_name)
    if node:
        return node.contextual_phrases
    return []


def get_equivalences(skill_name: str) -> List[str]:
    """Get equivalent/sibling skill IDs."""
    node = resolve_skill(skill_name)
    if node:
        return node.siblings + ([node.parent] if node.parent else []) + node.children
    return []


def classify_skills_by_domain(skill_names: List[str]) -> Dict[str, List[str]]:
    """
    Classify a list of skill names by domain.
    Used by role type detector (replaces the coupled _EVIDENCE_ALIASES check).
    """
    result: Dict[str, List[str]] = {}
    for name in skill_names:
        domain = get_skill_domain(name)
        if domain not in result:
            result[domain] = []
        result[domain].append(name)
    return result


def compute_domain_profile(skill_names: List[str]) -> Dict[str, float]:
    """
    Compute domain distribution for a set of skills.
    Returns {domain: ratio} showing what proportion of skills belong to each domain.
    Used by role type detector instead of assessable_ratio.
    """
    if not skill_names:
        return {}
    domain_counts: Dict[str, int] = {}
    for name in skill_names:
        domain = get_skill_domain(name)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    total = len(skill_names)
    return {d: c / total for d, c in domain_counts.items()}


def get_proficiency_anchor(level: int, domain: str = "general") -> str:
    """
    Get the proficiency description for a given level, optionally with domain example.
    Used to build cluster-specific LLM prompts.
    """
    anchor = PROFICIENCY_SCALE.get(level)
    if not anchor:
        return ""
    desc = f"{level} = {anchor.name}: {anchor.description}"
    if domain in anchor.examples:
        desc += f" Example: {anchor.examples[domain]}"
    return desc


def build_proficiency_scale_text(domain: str = "general") -> str:
    """
    Build the full proficiency scale text for an LLM prompt,
    using domain-specific examples where available.
    """
    lines = ["DEPTH SCALE WITH BEHAVIORAL ANCHORS:"]
    for level in range(6):
        lines.append(get_proficiency_anchor(level, domain))
    return "\n".join(lines)
