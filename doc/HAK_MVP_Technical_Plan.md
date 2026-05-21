# HAK — MVP Technical Plan
### AI-Powered Executive Search Operating System · UAE Market Focus

---

## Executive Summary

This document translates the HAK value proposition into a concrete, buildable MVP. The goal is to ship the smallest version of the platform that proves the core thesis: **an in-house TA team can run an executive search — from brief to ranked shortlist — without a search firm, in days instead of weeks.**

The MVP scope covers **Layers 1–2 and Layer 4** (Strategic, Execution, Analysis) for the **UAE market only**. Layer 3 (Outreach) and Layer 5 (Recommendation Command Centre) are deferred to v2 — they add value but aren't required to prove the core thesis.

**Target user:** A Head of TA or HR Director at a mid-to-large UAE-based organisation (500+ employees) hiring at Director/VP/C-suite level.

**MVP success metric:** A user can go from uploading a job brief to receiving a scored, evidence-backed shortlist of 15–30 candidates in under 48 hours of elapsed time, with less than 2 hours of active user effort.

---

## Part 1 — Architecture Overview

### 1.1 High-Level System Design

```
┌─────────────────────────────────────────────────────────┐
│                    CLIENT (Web App)                      │
│              Next.js 14+ / React / Tailwind              │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │  Brief    │  │ Company  │  │Candidate │  │Shortlist│ │
│  │  Builder  │  │ Universe │  │ Explorer │  │ & Score │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
└────────────────────────┬────────────────────────────────┘
                         │ REST / WebSocket
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   API LAYER (Backend)                     │
│               FastAPI (Python 3.12+)                     │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │  Search   │  │  Company │  │Candidate │  │  Score  │ │
│  │  Mandate  │  │  Builder │  │  Sourcer │  │ Engine  │ │
│  │  Service  │  │  Service │  │  Service │  │ Service │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
└──────┬──────────────┬──────────────┬──────────────┬─────┘
       │              │              │              │
       ▼              ▼              ▼              ▼
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐
│  LLM Layer │ │  External  │ │  Vector DB │ │ Primary  │
│  (Claude)  │ │  Data APIs │ │  (Pinecone │ │   DB     │
│            │ │  PDL/Apollo│ │  or Qdrant)│ │(Postgres)│
└────────────┘ └────────────┘ └────────────┘ └──────────┘
```

### 1.2 Technology Stack

| Layer | Technology | Why |
|---|---|---|
| **Frontend** | Next.js 14+ (App Router), React, Tailwind CSS, shadcn/ui | Fast iteration, SSR for dashboards, good DX |
| **Backend API** | FastAPI (Python 3.12+) | Async-native, great for AI/ML workloads, fast to build |
| **Primary DB** | PostgreSQL 16 (via Supabase or AWS RDS) | Relational integrity for searches, companies, candidates, scores |
| **Vector DB** | Qdrant (self-hosted) or Pinecone (managed) | Semantic search over company/candidate embeddings |
| **Cache** | Redis | Job queues, session cache, rate limiting |
| **Task Queue** | Celery + Redis (or Dramatiq) | Background processing for data enrichment, scoring runs |
| **LLM** | Anthropic Claude API (Sonnet for speed, Opus for deep analysis) | Brief parsing, company universe reasoning, candidate assessment |
| **Embedding Model** | Voyage AI or OpenAI text-embedding-3-large | Embedding briefs, companies, and candidate profiles for semantic matching |
| **People Data** | People Data Labs (primary) + Apollo.io (secondary) | PDL: 1.5B+ profiles, strong Middle East coverage. Apollo: contact data backup |
| **Company Data** | BoldData UAE (723K verified UAE companies) + Crustdata | UAE trade register data, firmographics, free zone coverage |
| **File Storage** | AWS S3 or Cloudflare R2 | CVs, brief documents, generated reports |
| **Auth** | Clerk or Supabase Auth | Multi-tenant, role-based access |
| **Hosting** | AWS (ECS/Fargate) or Railway for MVP speed | Containerised, auto-scaling |
| **Monitoring** | Sentry + PostHog | Error tracking + product analytics |

---

## Part 2 — Layer-by-Layer Implementation

### 2.1 Layer 1: Strategic (Brief Ingestion & Search Mandate)

**What it does:** Takes a raw job brief (PDF, Word doc, or structured form) and produces a weighted, validated Search Mandate that drives every downstream step.

#### 2.1.1 Brief Ingestion Pipeline

**Input formats accepted:**
- PDF/DOCX upload (position description, briefing notes)
- Structured form input (for users who prefer guided entry)
- Free-text paste

**Processing steps:**

```
Upload → Document Parser → LLM Extraction → Mandate Draft → User Validation → Locked Mandate
```

1. **Document Parsing:** Use `pymupdf` (PDF) and `python-docx` (Word) to extract raw text. For scanned PDFs, fall back to Tesseract OCR via `pytesseract`.

2. **LLM Extraction (Claude Sonnet):** Send extracted text with a structured prompt that asks Claude to extract and return JSON covering:

```json
{
  "role": {
    "title": "Chief Financial Officer",
    "level": "C-Suite",
    "reporting_to": "CEO",
    "direct_reports": 12,
    "location": "Dubai, UAE",
    "travel": "20% regional"
  },
  "requirements": {
    "must_have": [
      {
        "criterion": "15+ years in financial leadership roles",
        "weight": 0.95,
        "category": "experience"
      },
      {
        "criterion": "Experience in GCC/Middle East markets",
        "weight": 0.90,
        "category": "regional"
      }
    ],
    "nice_to_have": [...],
    "dealbreakers": [...]
  },
  "company_context": {
    "industry": "Real Estate Development",
    "size": "2000-5000 employees",
    "stage": "Growth",
    "culture_signals": [
      "Family-owned conglomerate transitioning to professional management",
      "Arabic and English working environment",
      "Hierarchical but modernising"
    ],
    "strategic_priorities": [
      "IPO readiness within 24 months",
      "Diversification into hospitality"
    ]
  },
  "compensation": {
    "base_range": { "min": 800000, "max": 1200000, "currency": "AED" },
    "package_notes": "Housing allowance, schooling, annual flights"
  },
  "target_sectors": {
    "direct": ["Real Estate Development", "Property Management"],
    "adjacent": ["Construction & Infrastructure", "Hospitality", "Banking & Finance (Real Estate Lending)"]
  }
}
```

3. **Company Culture Fingerprint:** A separate Claude call analyses the culture signals and produces a structured fingerprint:

```json
{
  "decision_making": "hierarchical_transitioning_to_collaborative",
  "pace": "fast_growth",
  "formality": "high",
  "innovation_orientation": "moderate",
  "risk_appetite": "conservative_but_opening",
  "diversity_profile": "multinational_workforce_local_leadership",
  "values": ["loyalty", "family", "excellence", "discretion"]
}
```

4. **Weight Calibration UI:** The user sees all extracted requirements in a drag-and-drop interface where they can adjust weights (0.0–1.0), add/remove criteria, and flag dealbreakers. This is critical — the human validates the AI's interpretation before any execution.

5. **Mandate Lock:** Once validated, the mandate is versioned and locked. All downstream processing references this specific mandate version.

#### 2.1.2 Data Model (PostgreSQL)

```sql
CREATE TABLE searches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID REFERENCES organisations(id),
  created_by UUID REFERENCES users(id),
  title VARCHAR(255) NOT NULL,
  status VARCHAR(50) DEFAULT 'draft',  -- draft, mandate_review, active, completed, archived
  mandate JSONB NOT NULL,              -- the full structured mandate
  mandate_version INTEGER DEFAULT 1,
  culture_fingerprint JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE search_criteria (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id UUID REFERENCES searches(id),
  criterion TEXT NOT NULL,
  category VARCHAR(50),   -- technical, experience, cultural, leadership, regional
  type VARCHAR(20),       -- must_have, nice_to_have, dealbreaker
  weight DECIMAL(3,2) DEFAULT 0.50,
  sort_order INTEGER
);
```

#### 2.1.3 Key Technical Decisions

- **Why Claude, not GPT, for brief parsing?** Executive briefs are nuanced documents full of implicit context (e.g., "family-owned group seeking a CFO" implies specific cultural dynamics in the UAE). Claude's longer context window and instruction-following are better suited for this structured extraction task. Use Sonnet for speed in the extraction pass, Opus if the brief is particularly complex or ambiguous.

- **Why structured JSON output?** Every downstream system (company builder, candidate scorer) consumes the mandate as structured data, not free text. This makes the system deterministic and auditable.

- **Embedding the mandate:** The full mandate text is also embedded (Voyage AI) and stored in the vector DB. This embedding is used later to semantically match companies and candidates against the "intent" of the search, not just keyword matches.

---

### 2.2 Layer 2: Execution (Company Universe + Candidate Discovery)

This is the engine. It has two stages: first build the company universe, then source candidates within it.

#### 2.2.1 Stage A — Company Universe Construction

**The core insight from the value proposition:** Traditional search is limited to obvious competitors. The platform should surface adjacent sectors — companies where the right talent exists but wouldn't appear in a naive search.

**Implementation:**

1. **Seed Companies from Mandate:**
   - Extract target sectors from the mandate (direct + adjacent)
   - Claude generates an initial list of 20–40 UAE-based company names per sector, with reasoning for each
   - Prompt includes UAE-specific context: free zones (DIFC, ADGM, DMCC, JAFZA), mainland vs. free zone distinctions, major family groups, sovereign wealth fund-linked entities

2. **Company Data Enrichment:**
   - For each company name, query BoldData UAE API to get: trade license number, jurisdiction, sector classification, founding date, employee count estimate, directors/executives
   - Cross-reference with Crustdata or D&B for: revenue estimates, headcount growth signals, recent funding, news
   - Store enriched company profiles in PostgreSQL + embed in vector DB

3. **Semantic Expansion:**
   - Embed each enriched company profile
   - Run similarity search against the mandate embedding to find additional companies in the vector DB that are semantically close but weren't in the initial seed list
   - This is how adjacent-sector companies get surfaced — a logistics company that recently moved into real estate development, for example

4. **Company Universe Presentation:**
   - Show the user a categorised list: Direct Sector, Adjacent Sector 1, Adjacent Sector 2, etc.
   - Each company card shows: name, sector, employee count, headquarters, a 1-line AI rationale ("This company is relevant because...")
   - User can include/exclude companies, add their own, and lock the universe

```sql
CREATE TABLE company_universe (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id UUID REFERENCES searches(id),
  company_id UUID REFERENCES companies(id),
  sector_category VARCHAR(50),    -- direct, adjacent_1, adjacent_2, user_added
  relevance_score DECIMAL(3,2),
  ai_rationale TEXT,
  status VARCHAR(20) DEFAULT 'included',  -- included, excluded
  added_by VARCHAR(20) DEFAULT 'ai'       -- ai, user
);

CREATE TABLE companies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  name_arabic VARCHAR(255),
  trade_license_number VARCHAR(100),
  jurisdiction VARCHAR(100),       -- DIFC, ADGM, Dubai Mainland, Abu Dhabi, etc.
  sector VARCHAR(255),
  sub_sector VARCHAR(255),
  employee_count_estimate INTEGER,
  founded_year INTEGER,
  headquarters_city VARCHAR(100),
  parent_group VARCHAR(255),       -- important in UAE (e.g., Al Futtaim, Majid Al Futtaim, Emaar)
  enrichment_data JSONB,
  embedding VECTOR(1536),          -- if using pgvector; or store in Qdrant
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.2.2 Stage B — Candidate Discovery

Once the company universe is locked, the system searches for executives within those companies.

**Implementation:**

1. **People Data Labs Query:**
   - For each company in the universe, query PDL's Person Search API with filters:
     - `company_name` or `company_domain`
     - `title_levels`: ["director", "vp", "c-suite", "partner", "owner"]
     - `location_country`: "AE" (and optionally neighbouring GCC countries)
   - PDL returns structured profiles: name, current title, company, location, work history, education, skills, LinkedIn URL

2. **Seniority Mapping:**
   - Not all titles map cleanly. "General Manager" in the UAE often equals "CEO" elsewhere. Build a UAE-specific title normalisation layer:

   ```python
   UAE_TITLE_MAP = {
       "general_manager": "c_suite",
       "managing_director": "c_suite",
       "group_head": "vp",
       "section_head": "director",
       "country_manager": "c_suite",
       # ... UAE-specific mappings
   }
   ```

3. **Candidate Deduplication:**
   - People appear in multiple data sources. Deduplicate on: LinkedIn URL (primary key), then fuzzy match on name + company + title
   - Use `thefuzz` (Python) for fuzzy string matching

4. **Initial Relevance Filter (fast, cheap):**
   - Before running expensive LLM scoring, do a vector similarity check: embed each candidate's profile summary, compare against the mandate embedding
   - Filter out candidates below a threshold (e.g., cosine similarity < 0.4)
   - This reduces the candidate pool from potentially hundreds to 50–80 for detailed scoring

5. **Candidate Storage:**

```sql
CREATE TABLE candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pdl_id VARCHAR(255),
  linkedin_url VARCHAR(500) UNIQUE,
  full_name VARCHAR(255) NOT NULL,
  full_name_arabic VARCHAR(255),
  current_title VARCHAR(255),
  current_company VARCHAR(255),
  location_city VARCHAR(100),
  location_country VARCHAR(10),
  seniority_level VARCHAR(50),       -- normalised: c_suite, vp, director, senior_manager
  work_history JSONB,
  education JSONB,
  skills JSONB,
  languages JSONB,                    -- critical for UAE market
  nationality VARCHAR(100),           -- relevant for Emiratisation considerations
  profile_summary TEXT,
  embedding VECTOR(1536),
  enrichment_data JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE search_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id UUID REFERENCES searches(id),
  candidate_id UUID REFERENCES candidates(id),
  source_company_id UUID REFERENCES companies(id),
  initial_relevance_score DECIMAL(3,2),
  status VARCHAR(30) DEFAULT 'discovered',
  -- discovered, shortlisted, assessed, recommended, rejected
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.2.3 UAE-Specific Considerations for Layer 2

The UAE market has unique characteristics that must be baked into the system:

- **Free zone vs. mainland:** Companies in DIFC operate under different employment law than Dubai mainland. This affects candidate expectations and mobility.
- **Emiratisation:** Some sectors have mandatory Emirati hiring quotas. The platform should flag whether a role falls under Emiratisation requirements and whether candidates hold UAE national status.
- **Family business groups:** Many of the largest UAE employers are family conglomerates (e.g., Al Ghurair, Al Futtaim, Majid Al Futtaim, Al Habtoor). The platform needs a `parent_group` field and should understand that talent within a family group often moves between subsidiaries.
- **Expatriate dynamics:** 90%+ of the UAE private-sector workforce is expatriate. Candidate profiles should capture visa status implications and typical package structures (base + housing + schooling + flights).
- **Arabic language:** Some roles require Arabic fluency. The system should handle Arabic names, company names, and language requirements natively.

---

### 2.3 Layer 4: Analysis (Candidate Assessment & Scoring)

This is the intellectual core of the platform — what replaces the search firm's subjective judgment with structured, evidence-backed assessment.

#### 2.3.1 Four-Dimensional Scoring Model

Every candidate is assessed across four dimensions, each scored 0–100 with evidence and confidence:

**Dimension 1: Technical Fit (weight from mandate)**

Scored against the weighted criteria in the search mandate. Each criterion gets a sub-score:

```json
{
  "dimension": "technical_fit",
  "overall_score": 78,
  "confidence": 0.72,
  "criteria_scores": [
    {
      "criterion": "15+ years in financial leadership roles",
      "score": 85,
      "evidence": "Work history shows 18 years in finance roles: 6 years as Group CFO at [Company], 4 years as Finance Director at [Company], 8 years in progressive finance roles at [Company].",
      "confidence": 0.90,
      "data_sources": ["pdl_work_history", "linkedin_profile"]
    },
    {
      "criterion": "Experience in GCC/Middle East markets",
      "score": 70,
      "evidence": "Based in Dubai for 9 years. Previous roles in Bahrain (2 years) and Saudi Arabia (3 years). Total GCC experience: 14 years.",
      "confidence": 0.85,
      "data_sources": ["pdl_work_history"]
    }
  ]
}
```

**Dimension 2: Cultural Fit (against company fingerprint)**

Claude analyses the candidate's career trajectory, company choices, and any available public signals against the company culture fingerprint:

- Has the candidate worked in similar organisational cultures before? (family-owned, corporate, startup)
- Does their career trajectory suggest alignment with the company's pace and formality?
- Language and regional fit

**Dimension 3: Trajectory & Readiness**

This assesses career momentum and readiness for the specific role:

- Is this a lateral move, step up, or step down?
- What's their typical tenure? (flight risk signal)
- Are they at a natural career inflection point?
- Time since last role change

**Dimension 4: Leadership Capability (C-suite roles only)**

For C-suite searches, an additional assessment of leadership signals:

- Board-level experience
- P&L ownership scale
- Transformation/turnaround experience
- Public profile (speaking, publications, awards)
- Team scale managed

#### 2.3.2 Scoring Implementation

```python
# Simplified scoring pipeline

class CandidateScorer:
    def __init__(self, search_mandate, culture_fingerprint):
        self.mandate = search_mandate
        self.culture = culture_fingerprint
        self.llm = AnthropicClient(model="claude-sonnet-4-20250514")
    
    async def score_candidate(self, candidate: Candidate) -> CandidateAssessment:
        # 1. Assemble all available data
        profile_context = self._build_profile_context(candidate)
        
        # 2. Technical Fit — scored per criterion
        technical = await self._score_technical_fit(profile_context)
        
        # 3. Cultural Fit — scored against fingerprint
        cultural = await self._score_cultural_fit(profile_context)
        
        # 4. Trajectory & Readiness
        trajectory = await self._score_trajectory(profile_context)
        
        # 5. Leadership (if C-suite search)
        leadership = None
        if self.mandate["role"]["level"] == "C-Suite":
            leadership = await self._score_leadership(profile_context)
        
        # 6. Compute weighted overall score
        overall = self._compute_overall(technical, cultural, trajectory, leadership)
        
        return CandidateAssessment(
            candidate_id=candidate.id,
            technical_fit=technical,
            cultural_fit=cultural,
            trajectory=trajectory,
            leadership=leadership,
            overall_score=overall.score,
            overall_confidence=overall.confidence,
            ai_summary=overall.narrative
        )
    
    async def _score_technical_fit(self, context: str) -> DimensionScore:
        prompt = f"""
        You are scoring a candidate against specific job requirements.
        
        SEARCH MANDATE CRITERIA (with weights):
        {json.dumps(self.mandate["requirements"], indent=2)}
        
        CANDIDATE PROFILE:
        {context}
        
        For EACH criterion, provide:
        - score (0-100)
        - evidence (specific facts from the profile that support the score)
        - confidence (0.0-1.0, based on data completeness)
        
        If there is insufficient data to score a criterion, set confidence to < 0.3
        and explain what data is missing.
        
        Respond in JSON format only.
        """
        response = await self.llm.complete(prompt)
        return parse_technical_scores(response)
```

#### 2.3.3 Confidence Scoring

Confidence is crucial — it tells the user how much to trust each score:

| Confidence Level | Meaning | Typical Scenario |
|---|---|---|
| 0.8–1.0 | High confidence | Rich work history data, multiple data points confirm |
| 0.5–0.79 | Moderate confidence | Some data available but gaps exist |
| 0.3–0.49 | Low confidence | Minimal data; score is largely inferred |
| 0.0–0.29 | Insufficient data | Cannot meaningfully score this dimension |

The UI should visually distinguish between high-confidence and low-confidence scores (e.g., solid vs. faded bars), so users know where to dig deeper.

#### 2.3.4 Assessment Storage

```sql
CREATE TABLE candidate_assessments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id UUID REFERENCES searches(id),
  candidate_id UUID REFERENCES candidates(id),
  assessment_version INTEGER DEFAULT 1,
  
  technical_fit_score DECIMAL(5,2),
  technical_fit_confidence DECIMAL(3,2),
  technical_fit_detail JSONB,
  
  cultural_fit_score DECIMAL(5,2),
  cultural_fit_confidence DECIMAL(3,2),
  cultural_fit_detail JSONB,
  
  trajectory_score DECIMAL(5,2),
  trajectory_confidence DECIMAL(3,2),
  trajectory_detail JSONB,
  
  leadership_score DECIMAL(5,2),
  leadership_confidence DECIMAL(3,2),
  leadership_detail JSONB,
  
  overall_score DECIMAL(5,2),
  overall_confidence DECIMAL(3,2),
  ai_narrative TEXT,           -- 2-3 paragraph summary of the candidate
  
  scoring_model_version VARCHAR(20),
  llm_model_used VARCHAR(50),
  tokens_consumed INTEGER,
  
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Part 3 — Data Strategy (UAE Focus)

### 3.1 Data Sources for MVP

| Data Need | Provider | Coverage | Cost Estimate (MVP) | Notes |
|---|---|---|---|---|
| **UAE Companies** | BoldData (CompanyData.com) | 723K verified UAE companies | ~$2,000–5,000/year | Official trade register data, includes free zones |
| **People/Candidates** | People Data Labs | 1.5B+ global profiles | From $98/mo (Pro) to custom enterprise | Good Middle East coverage; query by company + title + location |
| **Contact Enrichment** | Apollo.io | 275M+ contacts | From $49/mo | Backup for email/phone when PDL gaps |
| **Company Intelligence** | Crustdata | Real-time company data | Custom pricing | Headcount growth, funding signals, job posting data |
| **Company News/Signals** | Web search via API | N/A | Minimal | Press coverage, leadership changes |

### 3.2 Data Pipeline Architecture

```
                    ┌─────────────┐
                    │  BoldData   │
                    │  UAE Cos    │──────┐
                    └─────────────┘      │
                                         ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────────┐    ┌──────────┐
│  Claude LLM │───▶│  Company    │───▶│   PostgreSQL    │───▶│  Qdrant  │
│  (seed list)│    │  Enrichment │    │   (structured)  │    │ (vectors)│
└─────────────┘    │  Pipeline   │    └─────────────────┘    └──────────┘
                    └─────────────┘              │
                           ▲                     │
                    ┌──────┴──────┐              ▼
                    │  Crustdata  │    ┌─────────────────┐
                    │  (signals)  │    │  People Data    │
                    └─────────────┘    │  Labs Query     │
                                       │  (per company)  │
                                       └────────┬────────┘
                                                │
                                                ▼
                                       ┌─────────────────┐
                                       │  Candidate       │
                                       │  Profiles in DB  │
                                       └─────────────────┘
```

### 3.3 Data Freshness Strategy

For the MVP, a pragmatic approach:

- **Company data:** Bulk load BoldData UAE dataset at launch, refresh quarterly. Supplement with Crustdata for real-time signals on companies that appear in active searches.
- **Candidate data:** Query PDL on-demand per search (not pre-loaded). Each query returns fresh data. Cache results for 30 days. Re-enrich when a candidate appears in a new search.
- **Enrichment on-demand:** When a user clicks into a candidate profile, trigger a background enrichment job that pulls latest data from all sources.

### 3.4 UAE Data Compliance

- **UAE Federal Decree-Law No. 45 of 2021 (Data Protection Law):** Requires consent for processing personal data. For candidate sourcing, the platform operates on publicly available professional data (similar to how search firms use LinkedIn). Include a clear data processing disclosure.
- **DIFC Data Protection Law (DIFC Law No. 5 of 2020):** If serving DIFC-based clients, additional compliance requirements apply. Consult legal counsel before launch.
- **Candidate opt-out:** Implement a candidate data removal request mechanism (similar to PDL's opt-out) at a `hak.com/opt-out` endpoint.

---

## Part 4 — LLM Strategy & Prompt Architecture

### 4.1 Model Selection by Task

| Task | Model | Why | Estimated Cost/Search |
|---|---|---|---|
| Brief parsing & mandate extraction | Claude Sonnet | Structured extraction, fast, cheap | ~$0.50 |
| Company universe generation | Claude Sonnet | Creative reasoning about adjacent sectors | ~$1.00 |
| Company rationale writing | Claude Haiku | Simple, high-volume, low-complexity | ~$0.20 |
| Candidate technical scoring | Claude Sonnet | Nuanced assessment against criteria | ~$3.00 (for 50 candidates) |
| Cultural fit assessment | Claude Opus | Most nuanced task; requires deep inference | ~$5.00 (for 30 candidates) |
| Candidate narrative generation | Claude Sonnet | Writing 2-3 paragraph summaries | ~$2.00 |
| **Total per search** | | | **~$12–15** |

### 4.2 Prompt Architecture Principles

1. **Mandate as system prompt context:** Every LLM call that involves scoring or assessment includes the full search mandate in the system prompt. This ensures consistency across all candidate evaluations within a search.

2. **Evidence-first prompting:** All scoring prompts require the LLM to cite specific evidence before assigning a score. This prevents hallucinated assessments.

3. **Structured output:** All prompts request JSON output with a defined schema. Use Claude's tool-use feature (function calling) to enforce schema compliance.

4. **Calibration prompt:** Include 2-3 example scored candidates (synthetic) in the system prompt to calibrate the scoring scale. Without this, scores drift between runs.

5. **Batch processing:** Score candidates in batches of 5–10 per LLM call (where context window allows) to reduce API calls and maintain consistent scoring within a batch.

### 4.3 Handling LLM Limitations

- **Hallucination risk:** The LLM might infer qualifications the candidate doesn't have. Mitigate by requiring evidence citations and low confidence scores when data is sparse.
- **Scoring consistency:** Run a "calibration check" — score the same synthetic candidate profile across multiple runs and measure variance. If variance > 5 points, adjust the prompt.
- **Cost management:** Set a per-search token budget. Alert the user if a search with 200+ candidates will exceed cost thresholds. Offer a "deep assess top 30 only" option.

---

## Part 5 — Frontend MVP Screens

### 5.1 Core User Flows

**Flow 1: Create Search**
```
Dashboard → New Search → Upload Brief (or fill form) → Review Mandate → Adjust Weights → Lock Mandate
```

**Flow 2: Build Company Universe**
```
Locked Mandate → AI generates company list → User reviews (include/exclude) → Add companies manually → Lock Universe
```

**Flow 3: Discover & Assess Candidates**
```
Locked Universe → System runs candidate discovery (async, with progress bar) → Candidate list appears → User clicks "Score All" → Scoring runs in background → Results appear with scores, evidence, confidence
```

**Flow 4: Review Shortlist**
```
Scored candidates → Sort/filter by dimension → Click into candidate for full assessment → Compare candidates side-by-side → Export shortlist as PDF
```

### 5.2 Screen List (MVP)

1. **Dashboard** — Active searches, recent activity, saved candidates
2. **Search Setup** — Brief upload + mandate builder
3. **Mandate Review** — Weight adjustment, criteria editing, culture fingerprint
4. **Company Universe** — Card grid of companies, categorised by sector, with include/exclude toggle
5. **Candidate Pipeline** — Table/grid of discovered candidates with initial relevance scores
6. **Candidate Profile** — Full assessment view with four-dimension scores, evidence, narrative
7. **Shortlist View** — Ranked candidates, side-by-side comparison, 9-box talent map (Brief Fit vs. Readiness)
8. **Settings** — Organisation setup, user management, data source config

---

## Part 6 — MVP Build Plan

### 6.1 Phase Breakdown (12 Weeks)

**Weeks 1–2: Foundation**
- Set up monorepo (Next.js frontend + FastAPI backend)
- Database schema design and migration (PostgreSQL)
- Auth system (Clerk or Supabase Auth)
- API scaffolding (search CRUD, company CRUD, candidate CRUD)
- Deploy CI/CD pipeline (GitHub Actions → AWS/Railway)

**Weeks 3–4: Layer 1 — Strategic**
- Document upload and parsing pipeline
- Claude integration for mandate extraction
- Mandate review UI (weight sliders, criteria editing)
- Culture fingerprint generation
- Mandate versioning and locking

**Weeks 5–7: Layer 2 — Execution**
- Company universe generation (Claude + BoldData integration)
- Company enrichment pipeline (background jobs)
- Vector embedding pipeline (companies)
- Company universe UI (cards, filtering, include/exclude)
- PDL integration for candidate sourcing
- Candidate deduplication logic
- UAE title normalisation
- Initial relevance scoring (vector similarity)
- Candidate pipeline UI

**Weeks 8–10: Layer 4 — Analysis**
- Four-dimension scoring engine
- Prompt engineering and calibration
- Batch scoring pipeline (Celery workers)
- Candidate profile UI (full assessment view)
- Confidence scoring and visualisation
- Shortlist view with ranking and filtering

**Weeks 11–12: Polish & Launch Prep**
- 9-box talent map visualisation
- Side-by-side candidate comparison
- PDF export of shortlist
- Error handling, edge cases, loading states
- Performance optimisation
- Security audit (auth, data access, API keys)
- UAT with 2-3 test searches using real UAE roles

### 6.2 Team Required (MVP)

| Role | Count | Focus |
|---|---|---|
| Full-stack engineer | 1–2 | Next.js + FastAPI, integrations |
| AI/ML engineer | 1 | Prompt engineering, scoring calibration, embeddings |
| Designer | 0.5 (contract) | UI/UX for core flows |
| Product (you) | 1 | Domain expertise, testing, user validation |

### 6.3 MVP Cost Estimate

| Item | Monthly Cost | Notes |
|---|---|---|
| Hosting (AWS/Railway) | $200–500 | Small-scale MVP |
| PostgreSQL (managed) | $50–100 | Supabase or RDS |
| Qdrant (managed) | $100 | Or self-host on same infra |
| Claude API | $100–300 | ~20-30 test searches/month |
| People Data Labs | $98–500 | Depending on query volume |
| BoldData UAE | $200–400/mo | Amortised annual cost |
| Apollo.io | $49 | Backup contact data |
| Clerk Auth | $25 | Small user base |
| Domain, email, misc | $50 | |
| **Total infrastructure** | **~$900–2,000/mo** | |

Plus engineering salaries — this is the dominant cost. With a lean team of 2–3 engineers, the 12-week MVP build would cost approximately $50,000–$120,000 in total (depending on hiring model and geography).

---

## Part 7 — What's Deferred to v2

| Feature | Layer | Why Deferred |
|---|---|---|
| AI-drafted outreach messages | Layer 3 | Not needed to prove core thesis; can be added after MVP validation |
| Outreach tracking & pipeline | Layer 3 | Requires email integration, CRM-like features |
| Search Command Centre dashboard | Layer 5 | Nice-to-have visualisation; MVP shortlist view is sufficient |
| 9-box talent map (interactive) | Layer 5 | Static version in MVP; interactive version in v2 |
| Living candidate profiles (cross-search enrichment) | Platform | Requires multiple searches to accumulate data; build after 10+ searches |
| Multi-market expansion (KSA, Bahrain, etc.) | Platform | UAE-only for MVP; expand data sources per market |
| Client portal (external sharing) | Platform | MVP users export PDFs; v2 adds client-facing portal |
| Interview scheduling integration | Platform | Out of scope for search-focused MVP |
| Candidate response tracking | Layer 3 | Depends on outreach layer |
| Team collaboration (comments, assignments) | Platform | Single-user workflows first |

---

## Part 8 — Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **People Data Labs has poor UAE coverage** | Candidate discovery fails for smaller UAE companies | Test PDL coverage for 20 known UAE companies before committing. Have Apollo and Crustdata as fallbacks. Consider supplementing with manual LinkedIn research. |
| **LLM scoring is inconsistent** | Users lose trust in assessments | Invest heavily in prompt calibration (Week 8). Run 50+ test scores and measure variance. Add human override capability. |
| **BoldData UAE data is stale** | Company universe includes defunct companies | Cross-reference with Crustdata real-time signals. Add a "verify company" step in the UI. |
| **Cultural fit scoring is unreliable** | Core differentiator fails | Use cultural fit as a "signal" rather than a hard score in MVP. Label it "Indicative" with a clear disclaimer. Let users validate. |
| **UAE data protection compliance** | Legal risk | Engage UAE-based legal counsel before launch. Implement opt-out mechanism. Use only publicly available professional data. |
| **Cost per search exceeds expectations** | Unit economics don't work | Set token budgets per search. Offer "standard" vs. "deep" assessment tiers. Monitor costs per search in first 10 searches. |

---

## Part 9 — Success Criteria for MVP

The MVP is validated when:

1. **A real TA professional** can create a search, upload a brief, and get a scored shortlist without assistance from the engineering team.
2. **Time to shortlist** is under 48 hours (elapsed), with under 2 hours of active user time.
3. **Shortlist quality** — at least 60% of AI-surfaced candidates are deemed "worth reaching out to" by the TA professional (measured via user feedback on each candidate).
4. **Evidence quality** — users can read the scoring rationale and understand *why* each candidate was scored the way they were. No "trust us" black boxes.
5. **Cost per search** is under $50 in infrastructure costs (excluding salaries), proving the unit economics work.

---

*Document version: 1.0 · Created: May 2026 · HAK Talent Intelligence Platform*
