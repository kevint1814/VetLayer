"""
Skill Assessment Pipeline — The core intelligence engine of VetLayer.

Speed-optimized single-call architecture:
  - Only assesses job-relevant skills (6-12 skills, not all 30+)
  - Minimal output format (no evidence quotes — just depth + reasoning)
  - Deterministic evidence extraction post-LLM (no extra API calls)
  - Pipeline timing logs for every stage
  - Result caching to skip repeat analyses
  - Target: <10 seconds per analysis
"""

import re
import copy
import time
import hashlib
import logging
from collections import OrderedDict
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from app.utils.llm_client import llm_client

logger = logging.getLogger(__name__)

# Pipeline version — bump this when prompt, scoring, or output format changes
# so the cache doesn't return stale results after algorithm updates.
PIPELINE_VERSION = "v0.9"


# ═══════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Evidence:
    """A piece of evidence supporting a skill claim."""
    evidence_type: str
    description: str
    source_text: str
    strength: float = 0.5

@dataclass
class SkillAssessment:
    """Complete assessment of a single skill."""
    name: str
    category: str
    estimated_depth: int         # 0-5 (0 = not found)
    depth_confidence: float      # 0.0 - 1.0
    depth_reasoning: str
    evidence: List[Evidence] = field(default_factory=list)
    last_used_year: Optional[int] = None
    years_of_use: Optional[float] = None

@dataclass
class PipelineTimings:
    """Timing breakdown for each pipeline stage."""
    resume_format_ms: float = 0
    llm_call_ms: float = 0
    result_parse_ms: float = 0
    evidence_extraction_ms: float = 0
    total_ms: float = 0
    cache_hit: bool = False


# ═══════════════════════════════════════════════════════════════════════
# System Prompt — minimal output, maximum speed
# ═══════════════════════════════════════════════════════════════════════

FAST_ASSESSMENT_PROMPT = """You are VetLayer's Skill Assessment Engine. Expert technical recruiter with 15+ years of experience evaluating software engineers.

Given JOB SKILLS and a CANDIDATE RESUME, rate each skill's depth on this scale:

DEPTH SCALE WITH BEHAVIORAL ANCHORS:
0 = NOT FOUND: Skill not mentioned or evidenced anywhere on the resume, not even implicitly.
1 = AWARENESS: Skill listed in a skills section or mentioned in passing, but no concrete usage described. Example: "Familiar with Docker" with no project using it.
2 = BEGINNER: Used in coursework, tutorials, personal side projects, or briefly in a professional setting. Example: "Completed a React tutorial", "Used Redis in a hackathon."
3 = INTERMEDIATE/PROFESSIONAL: Used in production work with real users. Built or maintained features using this skill. 1+ years of regular hands on use. Example: "Built REST APIs with FastAPI serving production traffic", "Developed React components used by 10K+ users."
4 = ADVANCED: Led architecture decisions, designed systems, optimized performance, or mentored others in this skill. 2+ years of deep professional use. Example: "Architected microservices migration serving 2M users", "Led team adoption of Kubernetes, designed deployment pipelines."
5 = EXPERT: Industry recognized, published research, created widely adopted tools/libraries, or deep specialist. Conference speaker, open source maintainer, or core contributor. Example: "Created open source library with 5K+ stars", "Published paper on distributed consensus."

CRITICAL — IMPLIED SKILL RULES (you MUST follow these):
When a candidate has professional experience building production applications with a FRAMEWORK, they NECESSARILY have professional level skill in that framework's FOUNDATION technologies. This is not optional, it is logically required.

Specifically:
- React, Next.js, Vue, Angular, Svelte experience at depth 3+ implies HTML depth MUST be >=3, CSS depth MUST be >=3, JavaScript depth MUST be >=3, Browser APIs depth MUST be >=2
- Node.js, Express, Nest.js experience at depth 3+ implies JavaScript depth MUST be >=3
- Django, Flask, FastAPI experience at depth 3+ implies Python depth MUST be >=3
- Spring Boot experience at depth 3+ implies Java depth MUST be >=3
- Rails experience at depth 3+ implies Ruby depth MUST be >=3
- Laravel, Symfony, CodeIgniter, WordPress development at depth 3+ implies PHP depth MUST be >=3
- ASP.NET, .NET Core, Entity Framework at depth 3+ implies C# depth MUST be >=3
- React Native, Expo at depth 3+ implies React depth MUST be >=3, JavaScript depth MUST be >=3
- Flutter at depth 3+ implies Dart depth MUST be >=3
- Android development at depth 3+ implies Kotlin OR Java depth MUST be >=2
- iOS development at depth 3+ implies Swift depth MUST be >=2
- pandas, numpy, scikit-learn, TensorFlow, PyTorch at depth 3+ implies Python depth MUST be >=3
- Kubernetes at depth 3+ implies Docker depth MUST be >=2, Linux depth MUST be >=2
- Terraform at depth 3+ implies at least one cloud platform (AWS/GCP/Azure) depth MUST be >=2
- Any ORM (Prisma, Sequelize, SQLAlchemy, TypeORM, Entity Framework) at depth 3+ implies SQL depth MUST be >=2
- Any production web application work implies the primary language depth MUST be >=3
- Building web apps with caching, localStorage, sessionStorage, fetch, WebSockets, service workers, or any client side storage/networking implies Browser APIs depth MUST be >=2

Example: A developer who built production React apps professionally CANNOT have HTML at depth 2. React IS HTML+CSS+JS. Rate HTML >=3, CSS >=3, JS >=3, Browser APIs >=2.

CRITICAL — UMBRELLA TERM RESOLUTION:
Candidates often use umbrella terms instead of listing specific skills. You MUST infer constituent skills:
- "web development" or "web developer" → implies HTML, CSS, JavaScript at minimum depth 2+
- "full-stack development" or "full stack developer" → implies both frontend (HTML, CSS, JS) and backend (server language, SQL, REST API) at depth 2+
- "frontend development" or "frontend developer" → implies HTML, CSS, JavaScript at depth 2+
- "backend development" or "backend developer" → implies server language, SQL, REST API at depth 2+
- "mobile development" → implies mobile platform skills (Android/iOS/React Native/Flutter) at depth 2+
- "data engineering" → implies SQL, Python at depth 2+
- "DevOps" → implies Linux, Docker, CI/CD at depth 2+
- "cloud engineering" → implies at least one cloud platform (AWS/GCP/Azure) at depth 2+
- "UI/UX development" → implies HTML, CSS, JavaScript, responsive design at depth 2+
- "CMS development" → implies PHP or similar, HTML, CSS at depth 2+
- "API development" → implies REST API at depth 3+
- "leveraging AI" or "using AI tools" or "AI integration" → implies AI tools familiarity at depth 2+

Adjust depth based on context (years, project complexity, role seniority). If someone was a "Senior Full Stack Developer for 4 years", their constituent skill depths should be 3 to 4, not just 2.

IMPORTANT: Do NOT require the skill to be explicitly listed by name. Look for EVIDENCE in work descriptions, project details, technologies used, and job titles. If the resume shows production React work, that IS evidence of HTML/CSS/JS/Browser APIs proficiency. If the resume mentions "caching layer", "localStorage", "real time notifications", "WebSocket", that IS Browser APIs evidence.

CRITICAL — NON-TECHNICAL AND GENERAL TOOL SKILLS:
Some job listings include non-technical skills like "Microsoft Office", "Google Workspace", "AI tools", or "collaboration tools". These ARE valid skills to assess (unlike soft skills). Rate them based on evidence:
- If resume mentions using these tools in a professional context, rate depth 2 to 3
- If resume shows advanced usage (macros, pivot tables, automation), rate depth 3 to 4
- If not mentioned but the candidate has office/professional experience, you MAY infer basic proficiency (depth 1 to 2) for Microsoft Office and Google Workspace
- Do NOT rate d:0 just because these aren't "programming skills"

However, TRUE soft skills (communication, teamwork, leadership, time management) should still be rated d:0, c:0 with reasoning "Soft skill, not assessed."

CRITICAL — JOB-SPECIFIC REASONING:
Your reasoning text ("r" field) MUST be specific to what this candidate actually did, referencing concrete projects, companies, metrics, or achievements from their resume. Never write generic descriptions like "Used in production work" or "Has professional experience." Instead write things like "Led 15 person delivery team at Wipro managing $2M client accounts" or "Designed client experience framework at Cognizant serving Fortune 500 clients."

If the JOB TITLE is provided below, connect your reasoning to what matters for that role. For example, if the job is "Director, Client Experience", highlight client facing achievements, leadership scope, and strategic experience rather than just listing technologies.

Return compact JSON:
{"a":[{"n":"React","d":3,"c":0.8,"r":"Built 1600 line production React app at MOVZZ serving 10K users.","y":2024,"cat":"framework"},{"n":"SASS","d":0,"c":0,"r":"Not found on resume.","y":null,"cat":"unknown"}]}

Fields: n=name(exact as requested), d=depth(0 to 5), c=confidence(0 to 1), r=reasoning(1 concise sentence with SPECIFIC evidence from resume, plain language, NO dashes or special punctuation), y=last_used_year(integer or null), cat=category(one of: language, framework, library, database, cloud, devops, testing, tool, concept, data, mobile, ai, general_tool, methodology, security, enterprise, networking, unknown)
EVERY requested skill MUST appear in the output. Not found=d:0,c:0. Listed only=d:1,c:0.2.

IMPORTANT: In your reasoning text, never use dashes, emdashes, or endashes. Use commas or periods instead."""


# ═══════════════════════════════════════════════════════════════════════
# Evidence Extractor — deterministic, no LLM calls
# ═══════════════════════════════════════════════════════════════════════

# Skill name variants for evidence matching (keyword-level)
_EVIDENCE_ALIASES = {
    # ── Web fundamentals ──────────────────────────────────────────────
    "html": ["html", "html5", "html 5", "markup"],
    "css": ["css", "css3", "css 3", "stylesheets", "stylesheet", "tailwind",
            "bootstrap", "styled-components", "styled components", "emotion"],
    "sass/scss": ["sass", "scss", "sass/scss", "less", "stylus"],
    "javascript": ["javascript", "js", "ecmascript", "es6", "es2015", "vanilla js"],
    "typescript": ["typescript", "ts"],
    # ── Frontend frameworks ───────────────────────────────────────────
    "react": ["react", "react.js", "reactjs", "react native"],
    "vue": ["vue", "vue.js", "vuejs", "vue 2", "vue 3"],
    "angular": ["angular", "angular.js", "angularjs", "angular 2+"],
    "next.js": ["next.js", "nextjs", "next"],
    "nuxt.js": ["nuxt.js", "nuxtjs", "nuxt"],
    "svelte": ["svelte", "sveltekit"],
    "gatsby": ["gatsby", "gatsby.js"],
    "jquery": ["jquery", "j query"],
    "tailwind": ["tailwind", "tailwindcss", "tailwind css"],
    "bootstrap": ["bootstrap", "bootstrap 4", "bootstrap 5"],
    # ── Backend ───────────────────────────────────────────────────────
    "node.js": ["node.js", "nodejs", "node"],
    "python": ["python", "py", "python3"],
    "java": ["java", "jdk", "jvm", "j2ee"],
    "go": ["golang", "go lang", "go programming"],
    "php": ["php", "php7", "php8", "php 7", "php 8"],
    "ruby": ["ruby", "rb"],
    "c#": ["c#", "csharp", "c sharp"],
    "c++": ["c++", "cpp"],
    "c": ["c language", "ansi c"],
    "rust": ["rust", "rustlang"],
    "scala": ["scala"],
    "kotlin": ["kotlin"],
    "swift": ["swift", "swiftui"],
    "dart": ["dart"],
    "r": ["r language", "r programming", "rlang", "rstudio"],
    "perl": ["perl"],
    "elixir": ["elixir"],
    # ── Backend frameworks ────────────────────────────────────────────
    "fastapi": ["fastapi", "fast api"],
    "django": ["django", "drf", "django rest"],
    "flask": ["flask"],
    "express": ["express", "express.js", "expressjs"],
    "nestjs": ["nestjs", "nest.js", "nest js"],
    "spring boot": ["spring boot", "spring", "springboot", "spring framework"],
    "laravel": ["laravel"],
    "symfony": ["symfony"],
    "wordpress": ["wordpress", "wp theme", "wp plugin"],
    "drupal": ["drupal"],
    "rails": ["rails", "ruby on rails", "ror"],
    ".net": ["asp.net", "aspnet", ".net", "dotnet", ".net core", "entity framework"],
    "gin": ["gin", "gin-gonic"],
    "phoenix": ["phoenix", "phoenix framework"],
    # ── Mobile ────────────────────────────────────────────────────────
    "react native": ["react native", "react-native", "reactnative"],
    "flutter": ["flutter"],
    "android": ["android", "android sdk", "android studio"],
    "ios": ["ios", "uikit", "cocoa touch", "xcode"],
    "ionic": ["ionic"],
    "expo": ["expo"],
    # ── Databases ─────────────────────────────────────────────────────
    "postgresql": ["postgresql", "postgres", "psql"],
    "mongodb": ["mongodb", "mongo", "mongoose"],
    "mysql": ["mysql", "mariadb"],
    "redis": ["redis"],
    "sql": ["sql", "t-sql", "pl/sql", "stored procedure", "stored procedures"],
    "sqlite": ["sqlite", "sqlite3"],
    "oracle": ["oracle", "oracle db"],
    "cassandra": ["cassandra"],
    "dynamodb": ["dynamodb", "dynamo db"],
    "elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "firebase": ["firebase", "firestore"],
    "supabase": ["supabase"],
    "neo4j": ["neo4j", "graph database"],
    "prisma": ["prisma"],
    "sequelize": ["sequelize"],
    "sqlalchemy": ["sqlalchemy", "sql alchemy"],
    "typeorm": ["typeorm", "type orm"],
    # ── Messaging ─────────────────────────────────────────────────────
    "kafka": ["kafka", "apache kafka", "kafka streams"],
    "rabbitmq": ["rabbitmq", "rabbit mq", "amqp"],
    "celery": ["celery"],
    "sqs": ["sqs", "amazon sqs"],
    # ── DevOps / Cloud ────────────────────────────────────────────────
    "docker": ["docker", "containerization", "container", "docker-compose",
               "docker compose", "dockerfile"],
    "kubernetes": ["kubernetes", "k8s", "kube", "helm"],
    "aws": ["aws", "amazon web services", "ec2", "rds", "s3", "lambda",
            "ecs", "fargate", "cloudwatch", "cloudformation", "sagemaker"],
    "gcp": ["gcp", "google cloud", "bigquery", "cloud run", "gke"],
    "azure": ["azure", "microsoft azure", "azure devops", "azure functions"],
    "ci/cd": ["ci/cd", "ci cd", "cicd", "continuous integration", "continuous deployment",
              "github actions", "gitlab ci", "jenkins", "circleci", "travis ci",
              "bitbucket pipelines", "azure pipelines"],
    "terraform": ["terraform", "infrastructure as code", "iac"],
    "ansible": ["ansible", "ansible playbook"],
    "linux": ["linux", "ubuntu", "centos", "debian", "rhel", "unix",
              "bash scripting", "shell scripting", "bash", "shell"],
    "nginx": ["nginx", "reverse proxy"],
    "apache": ["apache", "httpd"],
    "prometheus": ["prometheus"],
    "grafana": ["grafana"],
    # ── Data / ML / AI ────────────────────────────────────────────────
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "tensorflow": ["tensorflow", "tf"],
    "pytorch": ["pytorch", "torch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "keras": ["keras"],
    "spark": ["spark", "apache spark", "pyspark"],
    "hadoop": ["hadoop", "hdfs", "mapreduce"],
    "airflow": ["airflow", "apache airflow"],
    "dbt": ["dbt", "data build tool"],
    "tableau": ["tableau"],
    "power bi": ["power bi", "powerbi"],
    "jupyter": ["jupyter", "jupyter notebook", "jupyterlab"],
    "machine learning": ["machine learning", "ml", "deep learning", "neural network",
                         "neural networks", "model training", "model inference"],
    "llm/ai": ["llm", "large language model", "chatgpt", "gpt", "openai",
               "langchain", "llamaindex", "prompt engineering", "generative ai",
               "gen ai", "ai assistant", "ai-powered", "copilot"],
    "data science": ["data science", "data analysis", "data analytics",
                     "data visualization", "exploratory data"],
    # ── Testing ───────────────────────────────────────────────────────
    "jest": ["jest"],
    "pytest": ["pytest"],
    "junit": ["junit"],
    "cypress": ["cypress"],
    "playwright": ["playwright"],
    "selenium": ["selenium", "webdriver"],
    "vitest": ["vitest"],
    "rspec": ["rspec"],
    "phpunit": ["phpunit"],
    # ── Tools ─────────────────────────────────────────────────────────
    "graphql": ["graphql", "graph ql"],
    "rest api": ["rest api", "restful", "rest apis", "rest", "api endpoint",
                 "api endpoints", "api integration"],
    "git": ["git", "github", "gitlab", "version control", "bitbucket"],
    "webpack": ["webpack", "vite", "rollup", "esbuild", "bundler", "parcel"],
    "agile": ["agile", "scrum", "kanban", "sprint"],
    "jira": ["jira"],
    "figma": ["figma"],
    "postman": ["postman"],
    "swagger": ["swagger", "openapi"],
    "microservices": ["microservices", "micro services", "microservice"],
    # ── General tools ─────────────────────────────────────────────────
    "microsoft office": ["microsoft office", "ms office", "office 365", "excel",
                         "powerpoint", "word", "outlook", "ms excel", "ms word"],
    "google workspace": ["google workspace", "g suite", "google docs",
                         "google sheets", "google slides"],
    "ai tools": ["ai tools", "ai-powered", "copilot", "chatgpt", "claude",
                 "generative ai", "gen ai", "ai automation", "ai assistant",
                 "leveraging ai", "using ai", "ai integration"],
    "adobe creative suite": ["photoshop", "illustrator", "lightroom", "indesign",
                             "after effects", "premiere pro", "creative suite",
                             "creative cloud", "adobe"],
    # ── Concepts ──────────────────────────────────────────────────────
    "responsive design": ["responsive", "responsive design", "mobile first",
                          "media queries", "mobile-friendly"],
    "accessibility": ["accessibility", "a11y", "wcag", "aria", "screen reader"],
    "seo": ["seo", "search engine optimization", "meta tags"],
    "security": ["security", "owasp", "authentication", "authorization",
                 "oauth", "jwt", "encryption", "ssl", "tls"],
    "websockets": ["websocket", "websockets", "socket.io", "real-time",
                   "real time communication"],
    "system design": ["system design", "distributed systems", "scalability",
                      "high availability", "load balancing"],
    "design patterns": ["design patterns", "solid", "clean architecture",
                        "mvc", "mvvm"],
    # ── Browser APIs ──────────────────────────────────────────────────
    "browser apis": [
        "browser api", "browser apis", "web api", "web apis",
        "web storage", "web storage api",
        "local storage", "localstorage",
        "session storage", "sessionstorage",
        "indexeddb", "indexed db",
        "service worker", "service workers",
        "web workers", "web worker",
        "fetch api", "xmlhttprequest", "xhr",
        "dom manipulation", "dom api",
        "web components", "shadow dom", "custom elements",
        "intersection observer", "mutation observer", "resize observer",
        "websocket api", "canvas", "webgl",
        "geolocation", "notification api",
        "clipboard api", "drag and drop",
        "file api", "history api",
        "broadcast channel", "performance api",
    ],
    # ── ERP / Enterprise ──────────────────────────────────────────────
    "sap": ["sap", "s/4hana", "sap hana", "abap", "sap erp", "sap fiori"],
    "oracle erp": ["oracle erp", "oracle cloud", "oracle fusion", "oracle ebs"],
    "netsuite": ["netsuite", "oracle netsuite", "suitescript"],
    "microsoft dynamics": ["dynamics 365", "dynamics crm", "dynamics nav", "d365", "microsoft dynamics"],
    "workday": ["workday", "workday hcm", "workday integration"],
    "servicenow": ["servicenow", "service now", "snow", "servicenow itsm"],
    # ── CRM ──────────────────────────────────────────────────────────
    "salesforce": ["salesforce", "sfdc", "apex", "soql", "visualforce",
                    "salesforce lightning", "salesforce crm", "force.com"],
    "hubspot": ["hubspot", "hub spot", "hubspot crm", "hubspot api"],
    "zoho": ["zoho", "zoho crm", "zoho one"],
    # ── Blockchain / Web3 ────────────────────────────────────────────
    "solidity": ["solidity", "smart contract", "smart contracts", "evm"],
    "ethereum": ["ethereum", "eth", "erc-20", "erc-721", "erc20", "erc721"],
    "web3": ["web3", "web3.js", "ethers.js", "web3js", "ethersjs", "dapp", "dapps"],
    "hardhat": ["hardhat", "truffle", "foundry", "ganache"],
    # ── Game Dev ─────────────────────────────────────────────────────
    "unity": ["unity", "unity3d", "unity engine", "unity editor", "unityscript"],
    "unreal engine": ["unreal", "unreal engine", "ue4", "ue5", "blueprint"],
    "godot": ["godot", "godot engine", "gdscript"],
    # ── Low-code / No-code ───────────────────────────────────────────
    "zapier": ["zapier", "zap", "zapier integration"],
    "make": ["make", "integromat", "make.com", "make scenario"],
    "retool": ["retool", "retool app"],
    "power apps": ["power apps", "powerapps", "power app"],
    "power automate": ["power automate", "power automate flow", "microsoft flow"],
    "airtable": ["airtable", "air table", "airtable api"],
    "notion": ["notion", "notion.so", "notion api"],
    "bubble": ["bubble", "bubble.io"],
    # ── Business Intelligence ────────────────────────────────────────
    "looker": ["looker", "looker studio", "lookml", "look ml"],
    "qlik": ["qlik", "qlikview", "qlik sense", "qliksense"],
    "metabase": ["metabase"],
    "superset": ["superset", "apache superset"],
    "sisense": ["sisense"],
    "google data studio": ["data studio", "google data studio"],
    # ── Cybersecurity ────────────────────────────────────────────────
    "splunk": ["splunk", "splunk enterprise", "spl", "splunk query"],
    "burp suite": ["burp suite", "burpsuite", "burp"],
    "metasploit": ["metasploit", "msf", "msfconsole"],
    "kali linux": ["kali linux", "kali", "parrot os"],
    "penetration testing": ["penetration testing", "pen testing", "pentest", "pentesting", "ethical hacking"],
    "siem": ["siem", "security information", "security monitoring"],
    "soc2": ["soc2", "soc 2", "soc2 compliance", "soc 2 type ii"],
    "iso 27001": ["iso 27001", "iso27001", "isms"],
    "vulnerability assessment": ["vulnerability assessment", "vulnerability scanning", "vuln scan"],
    "ids/ips": ["ids", "ips", "intrusion detection", "intrusion prevention"],
    # ── Networking ───────────────────────────────────────────────────
    "cisco": ["cisco", "ccna", "ccnp", "cisco ios", "cisco switch", "cisco router"],
    "networking": ["tcp/ip", "tcp ip", "networking", "osi model", "http", "https",
                    "network protocol", "network protocols", "subnetting"],
    "load balancing": ["load balancer", "load balancing", "haproxy", "f5", "nginx load"],
    "dns": ["dns", "route53", "route 53", "bind", "domain name"],
    "cdn": ["cdn", "cloudfront", "akamai", "fastly", "content delivery"],
    "vpn": ["vpn", "wireguard", "openvpn", "ipsec"],
    # ── Data Platforms ───────────────────────────────────────────────
    "snowflake": ["snowflake", "snowflake db", "snowsql", "snowpipe"],
    "bigquery": ["bigquery", "big query", "google bigquery", "bq"],
    "databricks": ["databricks", "data bricks", "databricks lakehouse", "dbricks"],
    "redshift": ["redshift", "amazon redshift", "aws redshift"],
    "fivetran": ["fivetran", "five tran"],
    "delta lake": ["delta lake", "deltalake", "delta table"],
    "iceberg": ["apache iceberg", "iceberg", "iceberg table"],
    "data lake": ["data lake", "datalake", "lakehouse", "data lakehouse"],
    "etl/elt": ["etl", "elt", "extract transform load", "data pipeline",
                 "data pipelines", "data ingestion", "data integration"],
    # ── MLOps / LLMOps ───────────────────────────────────────────────
    "mlflow": ["mlflow", "ml flow", "mlflow tracking", "model registry"],
    "wandb": ["wandb", "weights and biases", "w&b", "weights & biases"],
    "langchain": ["langchain", "lang chain", "langchain agent"],
    "llamaindex": ["llamaindex", "llama index", "llama_index"],
    "pinecone": ["pinecone", "pinecone db", "pinecone index"],
    "chroma": ["chroma", "chromadb", "chroma db"],
    "weaviate": ["weaviate", "weaviate db"],
    "milvus": ["milvus", "milvus db"],
    "vector database": ["vector database", "vector db", "vector store",
                         "vector search", "embedding store", "embeddings"],
    "prompt engineering": ["prompt engineering", "prompt design", "prompt tuning",
                           "prompt template", "few-shot", "chain-of-thought"],
    "rag": ["rag", "retrieval augmented generation", "retrieval augmented",
            "retrieval-augmented"],
    "fine-tuning": ["fine tuning", "fine-tuning", "model fine tuning",
                     "lora", "qlora", "peft", "model training"],
    "hugging face": ["hugging face", "huggingface", "hf transformers",
                      "transformers library", "hf hub"],
    "sagemaker": ["sagemaker", "aws sagemaker", "amazon sagemaker"],
    "vertex ai": ["vertex ai", "google vertex", "vertex"],
    "azure ml": ["azure ml", "azure machine learning"],
    "kubeflow": ["kubeflow", "kube flow", "kubeflow pipeline"],
    "feature store": ["feature store", "feast", "tecton"],
    # ── Methodology ──────────────────────────────────────────────────
    "agile": ["agile", "agile methodology", "agile development", "agile scrum"],
    "scrum": ["scrum", "scrum master", "sprint planning", "sprint review",
              "daily standup", "sprint retrospective", "scrum methodology"],
    "kanban": ["kanban", "kanban board", "wip limit"],
    "safe": ["safe", "scaled agile", "safe framework", "scaled agile framework",
             "pi planning"],
    "lean": ["lean", "lean methodology", "lean development", "lean startup"],
    # ── Project Management tools ─────────────────────────────────────
    "asana": ["asana"],
    "monday": ["monday.com", "monday"],
    "trello": ["trello"],
    "linear": ["linear", "linear app"],
    "clickup": ["clickup", "click up"],
    # ── Documentation ────────────────────────────────────────────────
    "technical writing": ["technical writing", "tech writing", "api documentation",
                          "documentation", "technical documentation", "api docs"],
    "markdown": ["markdown", "github markdown"],
    "latex": ["latex", "overleaf"],
    # ── Other ────────────────────────────────────────────────────────
    "grpc": ["grpc", "protocol buffers", "protobuf", "proto3"],
    "xml": ["xml", "xslt", "xpath", "xsd"],
    "shell scripting": ["bash scripting", "shell script", "shell scripting",
                         "zsh", "powershell", "batch script"],
}


# ── Contextual phrase → skill mapping (semantic evidence) ─────────────
# These phrases don't contain the skill name directly but strongly imply it.
# Each entry: skill_canonical → list of (phrase, evidence_strength) tuples.
# This catches cases like "built responsive user interfaces" → CSS evidence.
_CONTEXTUAL_EVIDENCE = {
    "html": [
        ("web page", 0.6), ("web pages", 0.6), ("web application", 0.6),
        ("web app", 0.6), ("landing page", 0.7), ("user interface", 0.5),
        ("frontend", 0.5), ("front-end", 0.5), ("front end", 0.5),
        ("markup", 0.7), ("template", 0.4), ("web portal", 0.6),
        ("website", 0.6), ("web site", 0.6),
    ],
    "css": [
        ("responsive", 0.7), ("responsive design", 0.8), ("mobile-friendly", 0.7),
        ("user interface", 0.5), ("ui design", 0.6), ("ui/ux", 0.5),
        ("styling", 0.7), ("layout", 0.5), ("animations", 0.5),
        ("pixel-perfect", 0.8), ("design system", 0.6),
        ("frontend", 0.4), ("front-end", 0.4),
    ],
    "javascript": [
        ("interactive", 0.5), ("dynamic web", 0.6), ("single page application", 0.7),
        ("spa", 0.6), ("frontend", 0.5), ("front-end", 0.5),
        ("client-side", 0.6), ("browser-based", 0.5),
    ],
    "php": [
        ("wordpress", 0.8), ("drupal", 0.7), ("joomla", 0.7),
        ("lamp stack", 0.9), ("content management system", 0.6),
        ("cms development", 0.6), ("woocommerce", 0.7),
        ("magento", 0.7), ("laravel", 0.9), ("symfony", 0.9),
        ("codeigniter", 0.9),
    ],
    "sql": [
        ("database queries", 0.7), ("relational database", 0.7),
        ("database design", 0.7), ("data modeling", 0.6),
        ("stored procedure", 0.8), ("database optimization", 0.7),
        ("query optimization", 0.8), ("database migration", 0.6),
        ("etl", 0.5), ("data warehouse", 0.6),
    ],
    "rest api": [
        ("api endpoint", 0.8), ("api endpoints", 0.8), ("api development", 0.8),
        ("api integration", 0.7), ("api design", 0.7), ("http requests", 0.6),
        ("backend services", 0.5), ("web services", 0.6),
        ("microservice", 0.6), ("third-party api", 0.6),
        ("third party api", 0.6), ("api consumption", 0.6),
    ],
    "docker": [
        ("containerized", 0.8), ("containerisation", 0.8), ("container orchestration", 0.7),
        ("docker-compose", 0.9), ("dockerfile", 0.9),
    ],
    "linux": [
        ("server administration", 0.6), ("server management", 0.6),
        ("deployment", 0.4), ("ssh", 0.6), ("command line", 0.5),
        ("terminal", 0.4), ("cron", 0.6), ("systemd", 0.7),
    ],
    "ci/cd": [
        ("automated deployment", 0.7), ("deployment pipeline", 0.8),
        ("build pipeline", 0.7), ("automated testing", 0.5),
        ("continuous delivery", 0.8), ("release pipeline", 0.7),
        ("devops pipeline", 0.7),
    ],
    "git": [
        ("version control", 0.8), ("code review", 0.5), ("pull request", 0.7),
        ("merge request", 0.7), ("branching strategy", 0.7),
    ],
    "python": [
        ("pandas", 0.8), ("numpy", 0.8), ("scipy", 0.7),
        ("jupyter", 0.7), ("pip", 0.6), ("flask", 0.9),
        ("django", 0.9), ("fastapi", 0.9),
    ],
    "machine learning": [
        ("model training", 0.8), ("model inference", 0.7), ("prediction model", 0.7),
        ("classification", 0.5), ("regression model", 0.6), ("neural network", 0.8),
        ("deep learning", 0.9), ("feature engineering", 0.7),
        ("hyperparameter", 0.8), ("training data", 0.6),
    ],
    "ai tools": [
        ("leveraging ai", 0.8), ("using ai", 0.7), ("ai-powered", 0.8),
        ("ai integration", 0.7), ("prompt engineering", 0.8),
        ("chatgpt", 0.7), ("copilot", 0.7), ("generative ai", 0.8),
        ("ai automation", 0.7), ("ai assistant", 0.7),
    ],
    "microsoft office": [
        ("spreadsheet", 0.5), ("excel", 0.8), ("powerpoint", 0.7),
        ("word document", 0.6), ("outlook", 0.6), ("office suite", 0.8),
    ],
    "responsive design": [
        ("mobile-friendly", 0.8), ("mobile first", 0.8), ("media queries", 0.9),
        ("cross-browser", 0.6), ("adaptive design", 0.7), ("fluid layout", 0.7),
    ],
    "security": [
        ("authentication", 0.6), ("authorization", 0.6), ("oauth", 0.7),
        ("jwt", 0.6), ("encryption", 0.6), ("ssl", 0.5), ("tls", 0.5),
        ("penetration testing", 0.8), ("vulnerability", 0.6),
    ],
    "agile": [
        ("sprint planning", 0.8), ("daily standup", 0.7), ("retrospective", 0.7),
        ("user stories", 0.7), ("backlog grooming", 0.7), ("product backlog", 0.6),
        ("story points", 0.7),
    ],
    # ── Enterprise / CRM ─────────────────────────────────────────────
    "salesforce": [
        ("crm integration", 0.8), ("sfdc", 0.9), ("salesforce implementation", 0.9),
        ("customer relationship management", 0.8), ("force.com", 0.9), ("apex trigger", 0.9),
    ],
    "sap": [
        ("enterprise resource planning", 0.8), ("erp implementation", 0.8),
        ("sap implementation", 0.9), ("s/4hana", 0.9), ("abap programming", 0.9),
    ],
    # ── Blockchain ───────────────────────────────────────────────────
    "solidity": [
        ("smart contract", 0.9), ("token contract", 0.9), ("erc-20", 0.9), ("erc-721", 0.9),
        ("blockchain development", 0.8), ("decentralized application", 0.7),
    ],
    "ethereum": [
        ("decentralized", 0.6), ("web3 development", 0.8), ("blockchain network", 0.7),
        ("dapp", 0.8), ("decentralized finance", 0.8), ("defi", 0.8),
    ],
    # ── Data Platforms ───────────────────────────────────────────────
    "snowflake": [
        ("cloud data warehouse", 0.9), ("data warehouse modernization", 0.8),
        ("snowflake implementation", 0.9),
    ],
    "etl/elt": [
        ("data pipeline", 0.8), ("data ingestion", 0.8), ("data transformation", 0.8),
        ("extract transform", 0.8), ("data integration", 0.8), ("data workflow", 0.7),
        ("batch processing", 0.7), ("streaming data", 0.7),
    ],
    "data lake": [
        ("data lakehouse", 0.9), ("lakehouse architecture", 0.8), ("raw data storage", 0.7),
        ("data lake implementation", 0.9),
    ],
    # ── MLOps / AI ───────────────────────────────────────────────────
    "langchain": [
        ("llm application", 0.8), ("llm agent", 0.8), ("ai agent", 0.8), ("chatbot development", 0.7),
        ("conversational ai", 0.8), ("rag pipeline", 0.8),
    ],
    "vector database": [
        ("embedding search", 0.8), ("semantic search", 0.8), ("similarity search", 0.8),
        ("vector index", 0.8), ("embedding store", 0.8),
    ],
    "prompt engineering": [
        ("prompt design", 0.8), ("prompt optimization", 0.8), ("few-shot learning", 0.7),
        ("chain of thought", 0.8), ("prompt template", 0.7),
    ],
    "rag": [
        ("retrieval augmented", 0.9), ("knowledge retrieval", 0.8),
        ("document retrieval", 0.8), ("context injection", 0.8),
    ],
    "fine-tuning": [
        ("model training", 0.8), ("model fine-tuning", 0.9), ("lora training", 0.9),
        ("transfer learning", 0.8), ("domain adaptation", 0.8),
    ],
    # ── Cybersecurity ────────────────────────────────────────────────
    "security": [
        ("security audit", 0.8), ("vulnerability assessment", 0.8), ("security review", 0.8),
        ("threat modeling", 0.8), ("security architecture", 0.8), ("security compliance", 0.7),
        ("data protection", 0.7), ("encryption implementation", 0.8),
    ],
    "penetration testing": [
        ("pen test", 0.9), ("ethical hacking", 0.8), ("security testing", 0.8),
        ("vulnerability exploitation", 0.8), ("red team", 0.8),
    ],
    # ── Methodology ──────────────────────────────────────────────────
    "agile": [
        ("agile environment", 0.7), ("agile team", 0.7), ("agile development", 0.8),
        ("iterative development", 0.7), ("sprint based", 0.8),
    ],
    "scrum": [
        ("scrum master", 0.9), ("sprint planning", 0.9), ("sprint review", 0.8),
        ("daily standup", 0.8), ("retrospective", 0.8), ("product backlog", 0.8),
    ],
    "kanban": [
        ("kanban board", 0.8), ("work in progress limit", 0.8), ("pull system", 0.7),
        ("visual workflow", 0.7),
    ],
    # ── Networking ───────────────────────────────────────────────────
    "networking": [
        ("network configuration", 0.8), ("network architecture", 0.8),
        ("network infrastructure", 0.8), ("tcp ip", 0.8), ("network security", 0.7),
        ("firewall configuration", 0.8), ("routing and switching", 0.8),
    ],
    "cisco": [
        ("cisco switch", 0.9), ("cisco router", 0.9), ("cisco network", 0.8),
        ("network switch configuration", 0.8),
    ],
}


def _get_skill_variants(skill_name: str) -> List[str]:
    """Get all text variants of a skill name for searching."""
    name_lower = skill_name.lower().strip()
    # Check alias map
    for canonical, variants in _EVIDENCE_ALIASES.items():
        if name_lower in variants or name_lower == canonical:
            return variants
    # Fallback: just the name itself and common suffixes
    variants = [name_lower]
    if "." in name_lower:
        variants.append(name_lower.replace(".", ""))  # "node.js" → "nodejs"
    if " " in name_lower:
        variants.append(name_lower.replace(" ", ""))  # "vue js" → "vuejs"
    return variants


def _get_contextual_phrases(skill_name: str) -> List[tuple]:
    """Get contextual phrases that imply this skill without naming it directly."""
    name_lower = skill_name.lower().strip()
    # Direct match
    if name_lower in _CONTEXTUAL_EVIDENCE:
        return _CONTEXTUAL_EVIDENCE[name_lower]
    # Check if the skill aliases to a canonical name that has contextual phrases
    for canonical, variants in _EVIDENCE_ALIASES.items():
        if name_lower in variants or name_lower == canonical:
            return _CONTEXTUAL_EVIDENCE.get(canonical, [])
    return []


def extract_evidence(skill_name: str, parsed_resume: dict) -> List[Evidence]:
    """
    Deterministic evidence extraction — searches resume text for lines
    mentioning a skill. No LLM call needed.

    Two-layer matching:
    1. Keyword matching: exact skill name variants (high strength)
    2. Contextual matching: phrases that imply the skill (lower strength)

    Returns a list of Evidence objects with source_text snippets.
    """
    evidence_list = []
    variants = _get_skill_variants(skill_name)

    # Build a regex pattern that matches any variant (word boundary)
    escaped = [re.escape(v) for v in variants]
    pattern = re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)

    # ── Layer 1: Keyword matching (original behavior, high confidence) ──

    # Search in experience descriptions
    for exp in (parsed_resume.get("experience") or []):
        desc = exp.get("description") or ""
        techs = exp.get("technologies") or []
        title = exp.get("title") or ""
        company = exp.get("company") or ""

        # Estimate role duration for strength weighting
        start = exp.get("start_date", "")
        end = exp.get("end_date", "")
        duration_factor = _estimate_role_duration_factor(start, end)

        # Check in description text
        if desc and pattern.search(desc):
            snippet = _extract_snippet(desc, pattern)
            evidence_list.append(Evidence(
                evidence_type="experience",
                description=f"Used in role: {title} @ {company}",
                source_text=snippet,
                strength=min(0.95, 0.85 * duration_factor),
            ))

        # Check in technologies list
        tech_str = ", ".join(techs)
        if tech_str and pattern.search(tech_str):
            evidence_list.append(Evidence(
                evidence_type="technology_used",
                description=f"Listed as technology at {company}",
                source_text=f"Technologies: {tech_str}",
                strength=min(0.85, 0.65 * duration_factor),
            ))

    # Search in projects
    for proj in (parsed_resume.get("projects") or []):
        proj_name = proj.get("name") or ""
        proj_desc = proj.get("description") or ""
        proj_techs = proj.get("technologies") or []

        if proj_desc and pattern.search(proj_desc):
            snippet = _extract_snippet(proj_desc, pattern)
            evidence_list.append(Evidence(
                evidence_type="project",
                description=f"Used in project: {proj_name}",
                source_text=snippet,
                strength=0.8,
            ))

        tech_str = ", ".join(proj_techs)
        if tech_str and pattern.search(tech_str):
            evidence_list.append(Evidence(
                evidence_type="project_technology",
                description=f"Listed in project: {proj_name}",
                source_text=f"Tech: {tech_str}",
                strength=0.6,
            ))

    # Check in skills_mentioned list — weight by list prominence
    skills_mentioned = parsed_resume.get("skills_mentioned") or []
    for skill in skills_mentioned:
        if pattern.search(skill):
            # Shorter skills lists = each skill is more prominent
            prominence = 0.5 if len(skills_mentioned) <= 10 else 0.35
            evidence_list.append(Evidence(
                evidence_type="skills_list",
                description="Listed in skills section",
                source_text=skill,
                strength=prominence,
            ))
            break  # Only one entry from skills list

    # Check in summary
    summary = parsed_resume.get("summary", "")
    if summary and pattern.search(summary):
        snippet = _extract_snippet(summary, pattern)
        evidence_list.append(Evidence(
            evidence_type="summary",
            description="Mentioned in professional summary",
            source_text=snippet,
            strength=0.5,
        ))

    # Check in certifications
    for cert in (parsed_resume.get("certifications") or []):
        cert_name = cert.get("name", "") if isinstance(cert, dict) else str(cert)
        if cert_name and pattern.search(cert_name):
            evidence_list.append(Evidence(
                evidence_type="certification",
                description=f"Certification: {cert_name}",
                source_text=cert_name,
                strength=0.85,
            ))

    # ── Layer 2: Contextual phrase matching (semantic evidence) ────────
    # Only if we didn't find strong keyword evidence already
    keyword_evidence_count = len(evidence_list)
    contextual_phrases = _get_contextual_phrases(skill_name)

    if contextual_phrases:
        for exp in (parsed_resume.get("experience") or []):
            desc = (exp.get("description") or "").lower()
            title = exp.get("title") or ""
            company = exp.get("company") or ""
            if not desc:
                continue

            for phrase, phrase_strength in contextual_phrases:
                if phrase.lower() in desc:
                    # Avoid duplicate if keyword already found this experience
                    snippet_text = _extract_phrase_snippet(
                        exp.get("description", ""), phrase
                    )
                    # Lower strength for contextual (implied, not explicit)
                    adj_strength = phrase_strength * 0.75
                    evidence_list.append(Evidence(
                        evidence_type="contextual",
                        description=f"Implied by context: '{phrase}' in role at {company}",
                        source_text=snippet_text,
                        strength=round(adj_strength, 2),
                    ))
                    break  # One contextual match per experience entry

        # Also check projects for contextual evidence
        for proj in (parsed_resume.get("projects") or []):
            proj_desc = (proj.get("description") or "").lower()
            proj_name = proj.get("name") or ""
            if not proj_desc:
                continue

            for phrase, phrase_strength in contextual_phrases:
                if phrase.lower() in proj_desc:
                    snippet_text = _extract_phrase_snippet(
                        proj.get("description", ""), phrase
                    )
                    adj_strength = phrase_strength * 0.65
                    evidence_list.append(Evidence(
                        evidence_type="contextual",
                        description=f"Implied by context: '{phrase}' in project {proj_name}",
                        source_text=snippet_text,
                        strength=round(adj_strength, 2),
                    ))
                    break

    # Deduplicate by source_text
    seen = set()
    unique_evidence = []
    for ev in evidence_list:
        key = ev.source_text[:80]
        if key not in seen:
            seen.add(key)
            unique_evidence.append(ev)

    return unique_evidence


def _estimate_role_duration_factor(start_date: str, end_date: str) -> float:
    """
    Estimate a duration factor (0.8 to 1.2) for evidence strength weighting.
    Longer roles = higher confidence in skill claims from that role.
    """
    import re as _re
    if not start_date:
        return 1.0

    start_match = _re.search(r'(20\d{2}|19\d{2})', str(start_date))
    if not start_match:
        return 1.0

    start_year = int(start_match.group(1))

    if end_date and ("present" in str(end_date).lower() or "current" in str(end_date).lower()):
        from datetime import datetime
        end_year = datetime.now().year
    else:
        end_match = _re.search(r'(20\d{2}|19\d{2})', str(end_date)) if end_date else None
        end_year = int(end_match.group(1)) if end_match else start_year + 1

    years = max(end_year - start_year, 0)

    if years >= 3:
        return 1.15  # Long tenure: high confidence
    elif years >= 1:
        return 1.0   # Normal
    else:
        return 0.85  # Short stint: slightly lower


def _extract_phrase_snippet(text: str, phrase: str, context_chars: int = 120) -> str:
    """Extract a snippet around a contextual phrase match."""
    idx = text.lower().find(phrase.lower())
    if idx < 0:
        return text[:150]
    start = max(0, idx - context_chars // 2)
    end = min(len(text), idx + len(phrase) + context_chars // 2)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _extract_snippet(text: str, pattern: re.Pattern, context_chars: int = 120) -> str:
    """Extract a relevant snippet around the first match of the pattern."""
    match = pattern.search(text)
    if not match:
        return text[:150]

    start = max(0, match.start() - context_chars // 2)
    end = min(len(text), match.end() + context_chars // 2)

    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet


# ═══════════════════════════════════════════════════════════════════════
# Result Cache — skip repeat analyses
# ═══════════════════════════════════════════════════════════════════════

class PipelineCache:
    """In-memory LRU cache for pipeline results. Keyed by hash(resume + skills)."""

    def __init__(self, max_size: int = 200):
        self._cache: OrderedDict[str, List[SkillAssessment]] = OrderedDict()
        self._max_size = max_size

    def _make_key(self, resume_text: str, skill_list: str) -> str:
        raw = f"{PIPELINE_VERSION}|||{resume_text}|||{skill_list}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, resume_text: str, skill_list: str) -> Optional[List[SkillAssessment]]:
        key = self._make_key(resume_text, skill_list)
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
            logger.info(f"Cache HIT: {key}")
            return [copy.deepcopy(a) for a in result]
        return None

    def put(self, resume_text: str, skill_list: str, assessments: List[SkillAssessment]):
        key = self._make_key(resume_text, skill_list)
        # Evict least recently used if full
        if len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        self._cache[key] = [copy.deepcopy(a) for a in assessments]
        logger.info(f"Cache PUT: {key} ({len(assessments)} assessments)")

    def clear(self):
        self._cache.clear()
        logger.info("Cache cleared")

    @property
    def size(self) -> int:
        return len(self._cache)


# Global cache instance
_pipeline_cache = PipelineCache()


# ═══════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════

class SkillPipeline:
    """
    Speed-optimized single-call pipeline with:
    - Deterministic evidence extraction (no extra LLM calls)
    - Stage-by-stage timing logs
    - Result caching
    Target: <10 seconds.
    """

    async def run(
        self,
        parsed_resume: dict,
        required_skills: list = None,
        preferred_skills: list = None,
        job_title: str = "",
    ) -> tuple:
        """
        Run job-focused skill assessment. Always 1 LLM call, minimal output.
        Returns: (assessments: List[SkillAssessment], timings: PipelineTimings)
        """
        timings = PipelineTimings()
        pipeline_start = time.time()

        # ── Stage 1: Format resume ────────────────────────────────────
        t0 = time.time()
        resume_text = self._format_resume_compact(parsed_resume)
        timings.resume_format_ms = (time.time() - t0) * 1000

        # ── Build skill list ──────────────────────────────────────────
        skill_names = []
        skill_metadata = {}  # name -> {"mode": "req"/"pref", "min_depth": int}
        if required_skills:
            for s in required_skills:
                name = s.get('skill', '')
                skill_names.append(name)
                skill_metadata[name] = {"mode": "req", "min_depth": s.get('min_depth', 2)}
        if preferred_skills:
            for s in preferred_skills:
                name = s.get('skill', '')
                skill_names.append(name)
                skill_metadata[name] = {"mode": "pref", "min_depth": 0}

        if not skill_names:
            logger.warning("No job skills to assess")
            return [], timings

        skill_list_text = ", ".join(skill_names)

        # ── Check cache ───────────────────────────────────────────────
        cache_key_skills = f"{job_title}|{skill_list_text}"
        cached = _pipeline_cache.get(resume_text, cache_key_skills)
        if cached is not None:
            timings.cache_hit = True
            timings.total_ms = (time.time() - pipeline_start) * 1000
            logger.info(f"⚡ Cache hit — returning {len(cached)} cached assessments in {timings.total_ms:.0f}ms")
            self._log_timings(timings, len(cached))
            return cached, timings

        logger.info(f"Fast assessment: {len(skill_names)} skills → 1 LLM call")

        # ── Stage 2: LLM call ─────────────────────────────────────────
        t0 = time.time()
        # Pass job title for JD-specific reasoning, but NOT required depths
        # (depth requirements would anchor/bias the LLM's independent assessment)
        job_context = f"JOB TITLE: {job_title}\n" if job_title else ""
        result = await llm_client.complete_json(
            system_prompt=FAST_ASSESSMENT_PROMPT,
            user_message=f"{job_context}Skills: {skill_list_text}\n\nResume:\n{resume_text}",
            max_tokens=2500,
        )
        timings.llm_call_ms = (time.time() - t0) * 1000

        # ── Stage 3: Parse results ────────────────────────────────────
        t0 = time.time()
        assessments = []
        for item in result.get("a", result.get("assessments", [])):
            name = item.get("n", item.get("name", "Unknown"))
            depth = item.get("d", item.get("estimated_depth", 0))
            confidence = item.get("c", item.get("depth_confidence", 0.0))
            reasoning = item.get("r", item.get("depth_reasoning", ""))
            # Sanitize LLM reasoning: strip dashes/emdashes from output
            reasoning = reasoning.replace("—", ", ").replace("–", ", ").replace(" - ", ", ")

            # Parse last_used_year from LLM response
            last_year = item.get("y", item.get("last_used_year"))
            if last_year and isinstance(last_year, (int, float)) and last_year > 2000:
                last_year = int(last_year)
            else:
                last_year = None

            category = item.get("cat", item.get("category", "unknown"))
            # Normalize category values
            valid_categories = {
                "language", "framework", "library", "database", "cloud",
                "devops", "testing", "tool", "concept", "data", "mobile",
                "ai", "general_tool", "methodology", "security", "enterprise",
                "networking", "unknown",
            }
            if category not in valid_categories:
                category = "unknown"

            assessments.append(SkillAssessment(
                name=name,
                category=category,
                estimated_depth=max(0, min(5, depth)),
                depth_confidence=max(0.0, min(1.0, confidence)),
                depth_reasoning=reasoning,
                evidence=[],
                last_used_year=last_year,
            ))
        timings.result_parse_ms = (time.time() - t0) * 1000

        # ── Stage 4: Deterministic evidence extraction ────────────────
        t0 = time.time()
        for assessment in assessments:
            if assessment.estimated_depth > 0:
                assessment.evidence = extract_evidence(assessment.name, parsed_resume)
                # Boost confidence based on evidence count
                if assessment.evidence:
                    ev_count = len(assessment.evidence)
                    # More evidence = higher confidence (but don't exceed 1.0)
                    evidence_boost = min(ev_count * 0.05, 0.15)
                    assessment.depth_confidence = min(1.0, assessment.depth_confidence + evidence_boost)
        timings.evidence_extraction_ms = (time.time() - t0) * 1000

        # ── Cache results ─────────────────────────────────────────────
        _pipeline_cache.put(resume_text, cache_key_skills, assessments)

        timings.total_ms = (time.time() - pipeline_start) * 1000
        self._log_timings(timings, len(assessments))

        return assessments, timings

    def _log_timings(self, timings: PipelineTimings, skill_count: int):
        """Log a clean timing breakdown for monitoring."""
        logger.info(
            f"\n{'═' * 50}\n"
            f"  PIPELINE TIMING BREAKDOWN\n"
            f"{'─' * 50}\n"
            f"  resume_format:       {timings.resume_format_ms:>7.1f}ms\n"
            f"  llm_call:            {timings.llm_call_ms:>7.1f}ms\n"
            f"  result_parse:        {timings.result_parse_ms:>7.1f}ms\n"
            f"  evidence_extraction: {timings.evidence_extraction_ms:>7.1f}ms\n"
            f"{'─' * 50}\n"
            f"  TOTAL:               {timings.total_ms:>7.1f}ms  "
            f"({'CACHE HIT' if timings.cache_hit else f'{skill_count} skills assessed'})\n"
            f"{'═' * 50}"
        )

    def _format_resume_compact(self, parsed_resume: dict) -> str:
        """Format resume compactly to minimize input tokens."""
        sections = []

        if parsed_resume.get("name"):
            sections.append(f"NAME: {parsed_resume['name']}")

        if parsed_resume.get("summary"):
            summary = parsed_resume['summary'][:500]
            sections.append(f"SUMMARY: {summary}")

        if parsed_resume.get("experience"):
            exp_lines = ["EXPERIENCE:"]
            for exp in parsed_resume["experience"]:
                title = exp.get("title", "")
                company = exp.get("company", "")
                dates = f"{exp.get('start_date', '?')} to {exp.get('end_date', '?')}"
                desc = (exp.get("description") or "")[:350]
                techs = ", ".join(exp.get("technologies") or [])
                exp_lines.append(f"  {title} @ {company} ({dates})")
                if desc:
                    exp_lines.append(f"  {desc}")
                if techs:
                    exp_lines.append(f"  Tech: {techs}")
            sections.append("\n".join(exp_lines))

        if parsed_resume.get("skills_mentioned"):
            sections.append(f"SKILLS: {', '.join(parsed_resume['skills_mentioned'])}")

        if parsed_resume.get("projects"):
            proj_lines = ["PROJECTS:"]
            for proj in parsed_resume["projects"]:
                name = proj.get("name", "")
                desc = (proj.get("description") or "")[:250]
                techs = ", ".join(proj.get("technologies") or [])
                proj_lines.append(f"  {name}: {desc}")
                if techs:
                    proj_lines.append(f"  Tech: {techs}")
            sections.append("\n".join(proj_lines))

        if parsed_resume.get("education"):
            edu = parsed_resume["education"][0] if parsed_resume["education"] else {}
            if edu:
                sections.append(f"EDUCATION: {edu.get('degree', '')} {edu.get('field', '')} @ {edu.get('institution', '')}")

        return "\n\n".join(sections)


def assessment_to_dict(assessment: SkillAssessment) -> dict:
    """Convert a SkillAssessment to a serializable dict."""
    return {
        "name": assessment.name,
        "category": assessment.category,
        "estimated_depth": assessment.estimated_depth,
        "depth_confidence": assessment.depth_confidence,
        "depth_reasoning": assessment.depth_reasoning,
        "evidence": [
            {
                "evidence_type": e.evidence_type,
                "description": e.description,
                "source_text": e.source_text,
                "strength": e.strength,
            }
            for e in assessment.evidence
        ],
        "last_used_year": assessment.last_used_year,
        "years_of_use": assessment.years_of_use,
    }


def timings_to_dict(timings: PipelineTimings) -> dict:
    """Convert PipelineTimings to a serializable dict."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "resume_format_ms": round(timings.resume_format_ms, 1),
        "llm_call_ms": round(timings.llm_call_ms, 1),
        "result_parse_ms": round(timings.result_parse_ms, 1),
        "evidence_extraction_ms": round(timings.evidence_extraction_ms, 1),
        "total_ms": round(timings.total_ms, 1),
        "cache_hit": timings.cache_hit,
    }


# Singleton
skill_pipeline = SkillPipeline()
