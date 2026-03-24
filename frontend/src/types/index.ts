/**
 * VetLayer TypeScript type definitions.
 */

export interface ParsedExperience {
  company?: string;
  title?: string;
  start_date?: string;
  end_date?: string;
  description?: string;
  technologies?: string[];
}

export interface ParsedEducation {
  institution?: string;
  degree?: string;
  field?: string;
  graduation_date?: string;
  gpa?: string;
}

export interface ParsedProject {
  name?: string;
  description?: string;
  technologies?: string[];
  url?: string;
}

export interface ParsedLink {
  url: string;
  label?: string;
}

export interface ParsedResume {
  summary?: string;
  experience?: ParsedExperience[];
  education?: ParsedEducation[];
  skills_mentioned?: string[];
  certifications?: Array<string | { name?: string; date?: string; issuer?: string }>;
  projects?: ParsedProject[];
  links?: Array<string | ParsedLink>;
}

export interface IntelligenceProfile {
  executive_summary?: string;
  seniority_level?: string;
  career_narrative?: string;
  strengths?: string[];
  considerations?: string[];
  skill_narrative?: string;
  skill_categories?: Record<string, string[]>;
  culture_signals?: string;
  ideal_roles?: string[];
  ideal_roles_narrative?: string;
  talking_points?: string[];
  career_timeline_briefs?: Array<{
    company: string;
    title: string;
    brief: string;
  }>;
}

export interface Candidate {
  id: string;
  name: string;
  email?: string;
  phone?: string;
  location?: string;
  resume_filename: string;
  years_experience?: number;
  education_level?: string;
  current_role?: string;
  current_company?: string;
  source?: string;
  processing_status?: string; // "processing" | "ready" | "failed"
  resume_parsed?: ParsedResume;
  intelligence_profile?: IntelligenceProfile;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  title: string;
  company?: string;
  department?: string;
  description: string;
  required_skills?: SkillRequirement[];
  preferred_skills?: SkillRequirement[];
  experience_range?: { min_years: number; max_years: number };
  education_requirements?: any;
  location?: string;
  remote_policy?: string;
  created_at: string;
  updated_at: string;
}

export interface SkillRequirement {
  skill: string;
  min_depth: number;
  weight: number;
}

export interface SkillAssessment {
  name: string;
  category: string;
  estimated_depth: number;
  depth_confidence: number;
  depth_reasoning: string;
  evidence: Evidence[];
  last_used_year?: number;
  years_of_use?: number;
}

export interface Evidence {
  evidence_type: string;
  description: string;
  source_text: string;
  strength: number;
}

export interface SkillBreakdownItem {
  required_depth: number;
  estimated_depth: number;
  matched_skill?: string;
  match: boolean;
  confidence: number;
  weight: number;
  recency_factor?: number;
  reasoning?: string;
  preferred?: boolean;
}

export interface ConfidenceInterval {
  low: number;
  high: number;
}

export interface UncertainSkill {
  skill: string;
  depth: number;
  confidence: number;
  flag: string;
}

export interface AnalysisResult {
  id: string;
  candidate_id: string;
  job_id: string;
  overall_score: number;
  skill_match_score: number;
  experience_score: number;
  education_score: number;
  depth_score: number;
  skill_breakdown?: Record<string, SkillBreakdownItem>;
  strengths?: string[];
  gaps?: string[];
  summary_text?: string;
  recommendation?: string;
  recruiter_override?: string;
  recruiter_notes?: string;
  is_overridden: boolean;
  llm_model_used?: string;
  processing_time_ms?: number;
  risk_flags: RiskFlag[];
  interview_questions: InterviewQuestion[];
  created_at: string;
  // New: confidence and explainability fields
  analysis_confidence?: number;
  confidence_interval?: ConfidenceInterval;
  uncertain_skills?: UncertainSkill[];
  confidence_note?: string;
  score_drivers?: string[];
  // New: role type and domain context
  role_type?: string;
  role_type_confidence?: number;
  domain_profile?: Record<string, number>;
}

export interface RiskFlag {
  id: string;
  flag_type: string;
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  description: string;
  evidence?: string;
  suggestion?: string;
  is_dismissed: boolean;
}

export interface InterviewQuestion {
  id: string;
  category: string;
  question: string;
  rationale: string;
  target_skill?: string;
  expected_depth?: number;
  priority: number;
  follow_ups?: string[];
}

// ── Bulk operation types ──────────────────────────────────────────────

export interface BulkDeleteResponse {
  deleted_count: number;
  failed_ids: string[];
  errors: Record<string, string>;
}

export interface BulkUploadResponse {
  created: Array<{
    id: string;
    name: string;
    resume_filename: string;
    current_role?: string;
    current_company?: string;
  }>;
  failed: Array<{ filename: string; error: string }>;
  total_created: number;
  total_failed: number;
}

// ── Batch Analysis types ──────────────────────────────────────────────

export interface BatchItemResult {
  candidate_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  analysis_id: string;
  overall_score: number;
  recommendation: string;
  processing_time_ms?: number;
  cached: boolean;
  error?: string;
}

export interface BatchAnalysisStatus {
  batch_id: string;
  status: "processing" | "completed" | "partial_failure" | "failed";
  total: number;
  completed: number;
  failed: number;
  cached: number;
  results: BatchItemResult[];
  elapsed_ms?: number;
  // Persistent fields (from DB)
  candidate_ids?: string[];
  job_ids?: string[];
  job_titles?: string[];
  candidate_count?: number;
  avg_score?: number;
  top_recommendation?: string;
  created_at?: string;
  completed_at?: string;
}

export interface RankedCandidate {
  rank: number;
  analysis_id: string;
  candidate_id: string;
  candidate_name: string;
  current_role?: string;
  current_company?: string;
  overall_score: number;
  skill_match_score: number;
  depth_score: number;
  recommendation: string;
  risk_flag_count: number;
  processing_time_ms?: number;
  created_at: string;
}

export interface RankedResults {
  job_id: string;
  job_title: string;
  job_company?: string;
  total_candidates: number;
  candidates: RankedCandidate[];
}
