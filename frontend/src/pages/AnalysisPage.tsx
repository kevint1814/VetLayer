import { useEffect, useState } from "react";
import { useParams, Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, Clock, Cpu, CheckCircle2, AlertTriangle, XCircle,
  TrendingUp, TrendingDown, Shield, MessageSquare, Star, Info,
  ChevronDown, ChevronUp, Trash2
} from "lucide-react";
import { analysisApi } from "@/services/api";
import type { AnalysisResult, SkillBreakdownItem } from "@/types";
import DepthBar from "@/components/common/DepthBar";
import RecommendationBadge from "@/components/common/RecommendationBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import clsx from "clsx";

const DEPTH_LABELS: Record<number, string> = {
  0: "Not Found",
  1: "Awareness",
  2: "Beginner",
  3: "Intermediate",
  4: "Advanced",
  5: "Expert",
};

function depthLabel(d: number) {
  return DEPTH_LABELS[d] || `Level ${d}`;
}

/** Animated circular score indicator */
function ScoreRing({ score, size = 120 }: { score: number; size?: number }) {
  const [animatedScore, setAnimatedScore] = useState(0);
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const percent = Math.round(score * 100);
  const targetOffset = circumference - (circumference * Math.min(animatedScore, 100)) / 100;

  useEffect(() => {
    let current = 0;
    const target = percent;
    const step = target / 40;
    const timer = setInterval(() => {
      current += step;
      if (current >= target) {
        setAnimatedScore(target);
        clearInterval(timer);
      } else {
        setAnimatedScore(Math.floor(current));
      }
    }, 20);
    return () => clearInterval(timer);
  }, [percent]);

  const color = percent >= 75 ? "#059669" : percent >= 60 ? "#2563eb" : percent >= 40 ? "#d97706" : "#dc2626";

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke="#f1f3f7" strokeWidth={10}
        />
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke={color} strokeWidth={10}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={targetOffset}
          className="score-ring"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold" style={{ color }}>{animatedScore}</span>
        <span className="text-2xs text-text-tertiary uppercase tracking-wider">Score</span>
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedSkills, setExpandedSkills] = useState<Set<string>>(new Set());
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // Context-aware navigation
  const fromSource = searchParams.get("from");
  const fromJobId = searchParams.get("jobId");
  const fromBatchId = searchParams.get("batchId");

  const getBackUrl = () => {
    if (fromSource === "ranked" && fromJobId) {
      return `/ranked/${fromJobId}${fromBatchId ? `?from=batch&batchId=${fromBatchId}` : ""}`;
    }
    if (fromSource === "batch" && fromBatchId) {
      return `/batch?detail=${fromBatchId}`;
    }
    if (analysis) {
      return `/candidates/${analysis.candidate_id}`;
    }
    return "/candidates";
  };

  const getBackLabel = () => {
    if (fromSource === "ranked") return "Back to Rankings";
    if (fromSource === "batch") return "Back to Batch Results";
    return "Back to Candidate";
  };

  const handleDelete = async () => {
    if (!id || !analysis) return;
    setDeleteLoading(true);
    try {
      await analysisApi.delete(id);
      navigate(getBackUrl());
    } catch (err) {
      console.error(err);
      setDeleteLoading(false);
      setShowDeleteConfirm(false);
    }
  };

  useEffect(() => {
    if (!id) return;
    analysisApi.get(id).then((r) => setAnalysis(r.data)).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  const toggleSkill = (name: string) => {
    setExpandedSkills((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  if (loading) {
    return (
      <div className="page-container">
        <div className="h-4 w-28 shimmer rounded-lg mb-6" />
        <div className="glass-card-solid p-6 mb-5">
          <div className="flex items-start justify-between">
            <div className="space-y-3">
              <div className="h-3 w-24 shimmer rounded" />
              <div className="flex items-center gap-4">
                <div className="w-[120px] h-[120px] shimmer rounded-full" />
                <div className="space-y-2">
                  <div className="h-8 w-32 shimmer rounded-lg" />
                  <div className="h-4 w-48 shimmer rounded-lg" />
                </div>
              </div>
            </div>
            <div className="space-y-2">
              <div className="h-3 w-20 shimmer rounded" />
              <div className="h-3 w-16 shimmer rounded" />
            </div>
          </div>
        </div>
        <div className="grid grid-cols-4 gap-3 mb-5">
          {[1,2,3,4].map(i => <div key={i} className="glass-card-solid p-4 shimmer h-24" />)}
        </div>
      </div>
    );
  }

  if (!analysis) {
    return (
      <div className="p-10 text-center">
        <div className="w-16 h-16 rounded-2xl bg-surface-tertiary flex items-center justify-center mx-auto mb-4">
          <AlertTriangle size={28} className="text-text-tertiary" />
        </div>
        <p className="text-text-secondary">Analysis not found.</p>
        <Link to="/candidates" className="text-sm text-brand-500 hover:text-brand-600 mt-2 inline-block">
          Back to candidates
        </Link>
      </div>
    );
  }

  const breakdown = (analysis.skill_breakdown || {}) as Record<string, SkillBreakdownItem>;

  const requiredSkills = Object.entries(breakdown)
    .filter(([name, data]) => !name.startsWith('_') && !data.preferred)
    .sort(([, a], [, b]) => b.weight - a.weight);
  const preferredSkills = Object.entries(breakdown)
    .filter(([name, data]) => !name.startsWith('_') && data.preferred)
    .sort(([, a], [, b]) => b.weight - a.weight);

  return (
    <div className="page-container animate-fade-in">
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete this analysis?"
        description="This will permanently remove this analysis, including all risk flags and interview questions. This cannot be undone."
        actionText="Delete"
        danger
        loading={deleteLoading}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />

      {/* ── Back + Delete ──────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-6">
        <Link
          to={getBackUrl()}
          className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary transition-colors"
        >
          <ArrowLeft size={15} />
          {getBackLabel()}
        </Link>
        <button
          onClick={() => setShowDeleteConfirm(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-text-tertiary hover:text-red-500 hover:bg-red-50 transition-all"
        >
          <Trash2 size={13} />
          Delete Analysis
        </button>
      </div>

      {/* ── Header: Score Ring + Recommendation ────────────────── */}
      <div className="glass-card-solid p-6 mb-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium text-text-tertiary uppercase tracking-wider mb-4">
              Analysis Result
            </p>
            <div className="flex items-center gap-6">
              <ScoreRing score={analysis.overall_score} />
              <div>
                <RecommendationBadge recommendation={analysis.recommendation || "pending"} size="md" />
                <p className="text-sm text-text-tertiary mt-2">Overall compatibility score</p>
              </div>
            </div>
          </div>

          {/* Meta */}
          <div className="text-right space-y-2">
            <div className="flex items-center gap-1.5 text-xs text-text-tertiary justify-end">
              <Cpu size={13} />
              VetLayer Engine
            </div>
            {analysis.processing_time_ms && (
              <div className="flex items-center gap-1.5 text-xs text-text-tertiary justify-end">
                <Clock size={13} />
                {(analysis.processing_time_ms / 1000).toFixed(1)}s processing
              </div>
            )}
            <div className="text-xs text-text-tertiary">
              {new Date(analysis.created_at).toLocaleDateString("en-US", {
                month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit"
              })}
            </div>
          </div>
        </div>

        {/* Summary */}
        {analysis.summary_text && (
          <div className="mt-6 pt-6 border-t border-surface-border">
            <p className="text-sm text-text-secondary leading-relaxed">
              {analysis.summary_text}
            </p>
          </div>
        )}

        {/* Analysis Confidence & Score Drivers */}
        {(analysis.analysis_confidence != null || analysis.score_drivers?.length) && (
          <div className="mt-4 pt-4 border-t border-surface-border space-y-3">
            {analysis.analysis_confidence != null && (
              <div className="flex items-center gap-3">
                <span className="text-xs text-text-tertiary">Analysis Confidence:</span>
                <span className={`text-xs font-medium ${
                  analysis.analysis_confidence >= 0.7 ? "text-emerald-600" :
                  analysis.analysis_confidence >= 0.4 ? "text-amber-600" : "text-red-600"
                }`}>{Math.round(analysis.analysis_confidence * 100)}%</span>
                {analysis.confidence_interval && (
                  <span className="text-xs text-text-tertiary">
                    Score range: {Math.round(analysis.confidence_interval.low * 100)}&ndash;{Math.round(analysis.confidence_interval.high * 100)}
                  </span>
                )}
                {analysis.confidence_note && (
                  <span className="text-xs text-amber-600 italic">{analysis.confidence_note}</span>
                )}
              </div>
            )}
            {analysis.score_drivers && analysis.score_drivers.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {analysis.score_drivers.map((driver, i) => (
                  <span key={i} className="text-xs px-2.5 py-1 bg-surface-secondary rounded-full text-text-secondary">
                    {driver}
                  </span>
                ))}
              </div>
            )}
            {analysis.role_type && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-tertiary">Role Classification:</span>
                <span className="text-xs px-2 py-0.5 bg-surface-secondary rounded-full text-text-secondary font-medium">
                  {analysis.role_type === "skill_heavy" ? "Skill-Heavy" :
                   analysis.role_type === "experience_heavy" ? "Experience-Heavy" : "Hybrid"}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Score breakdown row ───────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        {[
          { label: "Skill Match", value: analysis.skill_match_score, icon: CheckCircle2, color: "text-emerald-600" },
          { label: "Depth Score", value: analysis.depth_score, icon: TrendingUp, color: "text-blue-600" },
          { label: "Experience", value: analysis.experience_score, icon: Clock, color: "text-violet-600" },
          { label: "Education", value: analysis.education_score, icon: Shield, color: "text-amber-600" },
        ].map(({ label, value, icon: Icon, color }, i) => (
          <div
            key={label}
            className="glass-card-solid p-4 text-center hover-lift stagger-item"
            style={{ animationDelay: `${i * 100}ms` }}
          >
            <Icon size={18} className={`mx-auto mb-2 ${color}`} />
            <p className="text-2xl font-bold text-text-primary">{Math.round(value * 100)}</p>
            <p className="text-xs text-text-tertiary mt-1">{label}</p>
            {/* Mini bar */}
            <div className="w-full h-1.5 bg-surface-tertiary rounded-full mt-2 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-1000 ease-out ${
                  value >= 0.75 ? "bg-emerald-500" : value >= 0.6 ? "bg-blue-500" : value >= 0.4 ? "bg-amber-500" : "bg-red-500"
                }`}
                style={{ width: `${Math.round(value * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* ── Skill Breakdown ──────────────────────────────────────── */}
      <div className="glass-card-solid p-5 mb-5">
        <h2 className="text-sm font-semibold text-text-primary mb-5">Required Skills Assessment</h2>
        <div className="space-y-3">
          {requiredSkills.map(([name, data], i) => {
            const isExpanded = expandedSkills.has(name);
            const shortfall = data.required_depth - data.estimated_depth;
            return (
              <div
                key={name}
                className={clsx(
                  "rounded-xl border transition-all duration-200 stagger-item",
                  data.match
                    ? "border-emerald-100 bg-emerald-50/30"
                    : data.estimated_depth === 0
                      ? "border-red-100 bg-red-50/30"
                      : "border-amber-100 bg-amber-50/30"
                )}
                style={{ animationDelay: `${i * 50}ms` }}
              >
                <button
                  type="button"
                  className="w-full p-4 flex items-center justify-between cursor-pointer"
                  onClick={() => toggleSkill(name)}
                >
                  <div className="flex items-center gap-2.5">
                    {data.match ? (
                      <CheckCircle2 size={16} className="text-status-success flex-shrink-0" />
                    ) : data.estimated_depth === 0 ? (
                      <XCircle size={16} className="text-status-danger flex-shrink-0" />
                    ) : (
                      <AlertTriangle size={16} className="text-status-warning flex-shrink-0" />
                    )}
                    <span className="text-sm font-medium text-text-primary">{name}</span>
                    {data.match && data.estimated_depth >= data.required_depth + 1 && (
                      <span className="text-2xs bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded font-medium">
                        Exceeds
                      </span>
                    )}
                    {!data.match && data.estimated_depth > 0 && (
                      <span className="text-2xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-medium">
                        {shortfall} level{shortfall > 1 ? "s" : ""} below
                      </span>
                    )}
                    {data.estimated_depth === 0 && (
                      <span className="text-2xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-medium">
                        Not found
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <span className={clsx(
                        "text-xs font-semibold",
                        data.match ? "text-status-success" : "text-status-danger"
                      )}>
                        {depthLabel(data.estimated_depth)}
                      </span>
                      <span className="text-2xs text-text-tertiary ml-1.5">
                        / {depthLabel(data.required_depth)} needed
                      </span>
                    </div>
                    {isExpanded ? (
                      <ChevronUp size={14} className="text-text-tertiary" />
                    ) : (
                      <ChevronDown size={14} className="text-text-tertiary" />
                    )}
                  </div>
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="px-4 pb-4 space-y-3 animate-fade-in">
                    <DepthBar
                      depth={data.estimated_depth}
                      required={data.required_depth}
                      confidence={data.confidence}
                    />
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
                      <div className="bg-white/60 rounded-lg p-2">
                        <p className="text-lg font-bold text-text-primary">{data.estimated_depth}/5</p>
                        <p className="text-2xs text-text-tertiary">Candidate Depth</p>
                      </div>
                      <div className="bg-white/60 rounded-lg p-2">
                        <p className="text-lg font-bold text-text-primary">{data.required_depth}/5</p>
                        <p className="text-2xs text-text-tertiary">Required Depth</p>
                      </div>
                      <div className="bg-white/60 rounded-lg p-2">
                        <p className="text-lg font-bold text-text-primary">{Math.round(data.confidence * 100)}%</p>
                        <p className="text-2xs text-text-tertiary">Confidence</p>
                      </div>
                      {data.recency_factor !== undefined && (
                        <div className="bg-white/60 rounded-lg p-2">
                          <p className="text-lg font-bold text-text-primary">{Math.round(data.recency_factor * 100)}%</p>
                          <p className="text-2xs text-text-tertiary">Recency</p>
                        </div>
                      )}
                    </div>
                    {data.reasoning && (
                      <div className="flex items-start gap-2 bg-white/60 rounded-lg p-3">
                        <Info size={14} className="text-brand-400 mt-0.5 flex-shrink-0" />
                        <p className="text-xs text-text-secondary leading-relaxed">
                          <span className="font-medium text-text-primary">Reasoning: </span>
                          {data.reasoning}
                        </p>
                      </div>
                    )}
                    {data.matched_skill && data.matched_skill !== name && (
                      <p className="text-2xs text-text-tertiary">
                        Matched via alias: <span className="font-medium">{data.matched_skill}</span>
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {requiredSkills.length === 0 && (
          <p className="text-sm text-text-tertiary">No skill requirements defined for this job.</p>
        )}

        {/* Preferred skills */}
        {preferredSkills.length > 0 && (
          <div className="mt-6 pt-6 border-t border-surface-border">
            <div className="flex items-center gap-2 mb-4">
              <Star size={15} className="text-brand-400" />
              <h3 className="text-sm font-semibold text-text-primary">Preferred Skills (Nice to Have)</h3>
            </div>
            <div className="space-y-2">
              {preferredSkills.map(([name, data]) => (
                <div key={name} className="flex items-center justify-between p-3 rounded-lg bg-brand-50/40 border border-brand-100 hover:bg-brand-50/60 transition-colors">
                  <div className="flex items-center gap-2.5">
                    <CheckCircle2 size={14} className="text-brand-500" />
                    <span className="text-sm font-medium text-text-primary">{name}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-brand-600">
                      {depthLabel(data.estimated_depth)}
                    </span>
                    <span className="text-2xs text-text-tertiary">
                      ({Math.round(data.confidence * 100)}% conf.)
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Uncertain Skills (verify in interview) ─────────────── */}
      {analysis.uncertain_skills && analysis.uncertain_skills.length > 0 && (
        <div className="glass-card-solid p-5 mb-5">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle size={16} className="text-amber-500" />
            <h2 className="text-sm font-semibold text-text-primary">Uncertain Assessments</h2>
            <span className="text-2xs text-text-tertiary ml-1">
              {analysis.uncertain_skills.length} skill{analysis.uncertain_skills.length !== 1 ? "s" : ""} need interview verification
            </span>
          </div>
          <div className="space-y-2">
            {analysis.uncertain_skills.map((us, i) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-amber-50/40 border border-amber-100">
                <div className="flex items-center gap-2.5">
                  <Info size={14} className="text-amber-500 flex-shrink-0" />
                  <span className="text-sm font-medium text-text-primary">{us.skill}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-text-secondary">
                    Depth {us.depth}/5
                  </span>
                  <span className="text-xs text-amber-600 font-medium">
                    {Math.round(us.confidence * 100)}% confidence
                  </span>
                  <span className="text-2xs text-amber-500 italic">{us.flag}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">
        {/* ── Strengths ───────────────────────────────────────── */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={16} className="text-status-success" />
            <h2 className="text-sm font-semibold text-text-primary">Strengths</h2>
            {analysis.strengths && analysis.strengths.length > 0 && (
              <span className="text-2xs bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full font-medium">
                {analysis.strengths.length}
              </span>
            )}
          </div>
          {analysis.strengths && analysis.strengths.length > 0 ? (
            <ul className="space-y-3">
              {analysis.strengths.map((s, i) => (
                <li key={i} className="flex items-start gap-2.5 p-3 bg-emerald-50/40 rounded-lg stagger-item" style={{ animationDelay: `${i * 60}ms` }}>
                  <CheckCircle2 size={15} className="text-status-success mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-text-secondary leading-relaxed">{s}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-text-tertiary">No notable strengths identified.</p>
          )}
        </div>

        {/* ── Gaps ──────────────────────────────────────────── */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingDown size={16} className="text-status-warning" />
            <h2 className="text-sm font-semibold text-text-primary">Gaps</h2>
            {analysis.gaps && analysis.gaps.length > 0 && (
              <span className="text-2xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
                {analysis.gaps.length}
              </span>
            )}
          </div>
          {analysis.gaps && analysis.gaps.length > 0 ? (
            <ul className="space-y-3">
              {analysis.gaps.map((g, i) => (
                <li key={i} className="flex items-start gap-2.5 p-3 bg-amber-50/40 rounded-lg stagger-item" style={{ animationDelay: `${i * 60}ms` }}>
                  <AlertTriangle size={15} className="text-status-warning mt-0.5 flex-shrink-0" />
                  <span className="text-sm text-text-secondary leading-relaxed">{g}</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex items-center gap-2.5 p-3 bg-emerald-50/40 rounded-lg">
              <CheckCircle2 size={15} className="text-status-success" />
              <p className="text-sm text-emerald-700 font-medium">No gaps identified. Strong match.</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Risk Assessment ──────────────────────────────────────── */}
      <div className="glass-card-solid p-5 mb-5">
        <div className="flex items-center gap-2 mb-5">
          <Shield size={16} className="text-text-tertiary" />
          <h2 className="text-sm font-semibold text-text-primary">Risk Assessment</h2>
          {analysis.risk_flags && analysis.risk_flags.length > 0 && (
            <span className="text-2xs text-text-tertiary ml-1">
              {analysis.risk_flags.length} flag{analysis.risk_flags.length !== 1 ? "s" : ""} identified
            </span>
          )}
        </div>
        {analysis.risk_flags && analysis.risk_flags.length > 0 ? (
          <div className="divide-y divide-surface-border">
            {analysis.risk_flags.map((f, i) => (
              <div key={f.id} className={clsx(
                "py-4 pl-5 stagger-item border-l-4",
                f.severity === "critical" && "border-l-red-500",
                f.severity === "high" && "border-l-orange-400",
                f.severity === "medium" && "border-l-amber-400",
                f.severity === "low" && "border-l-gray-300",
              )} style={{ animationDelay: `${i * 60}ms` }}>
                <p className="text-2xs uppercase tracking-wider text-text-tertiary font-medium mb-1">
                  {f.severity} · {f.flag_type.replace(/_/g, " ")}
                </p>
                <p className="text-sm font-semibold text-text-primary">{f.title}</p>
                <p className="text-sm text-text-secondary mt-1 leading-relaxed">{f.description}</p>
                {f.evidence && (
                  <p className="text-xs text-text-tertiary mt-2.5">
                    <span className="font-medium text-text-secondary">Evidence:</span> {f.evidence}
                  </p>
                )}
                {f.suggestion && (
                  <p className="text-xs text-text-tertiary mt-1.5">
                    <span className="font-medium text-text-secondary">Recommendation:</span> {f.suggestion}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-3 py-3">
            <CheckCircle2 size={16} className="text-status-success" />
            <p className="text-sm text-status-success font-medium">No risk flags detected — clean profile</p>
          </div>
        )}
      </div>

      {/* ── Interview Guide ────────────────────────────────────────── */}
      <div className="glass-card-solid p-5">
        <div className="flex items-center gap-2 mb-5">
          <MessageSquare size={16} className="text-text-tertiary" />
          <h2 className="text-sm font-semibold text-text-primary">Interview Guide</h2>
          {analysis.interview_questions && analysis.interview_questions.length > 0 && (
            <span className="text-2xs text-text-tertiary ml-1">
              {analysis.interview_questions.length} question{analysis.interview_questions.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
        {analysis.interview_questions && analysis.interview_questions.length > 0 ? (
          <div className="divide-y divide-surface-border">
            {analysis.interview_questions.map((q, i) => {
              const categoryLabel =
                q.category === "depth_probe" ? "Depth Probe" :
                q.category === "gap_exploration" ? "Gap Exploration" :
                q.category === "red_flag" ? "Red Flag" :
                q.category === "skill_verification" ? "Strength Check" :
                q.category === "behavioral" ? "Behavioral" :
                q.category === "finance" ? "Finance" :
                q.category === "compliance" ? "Compliance" :
                q.category === "operations" ? "Operations" :
                q.category === "hr" ? "HR" :
                q.category === "strategy" ? "Strategy" :
                q.category === "domain_specific" ? "Domain" :
                q.category === "leadership" ? "Leadership" :
                q.category === "trajectory" ? "Career Trajectory" :
                q.category.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
              return (
                <div
                  key={q.id}
                  className="py-4 stagger-item"
                  style={{ animationDelay: `${i * 60}ms` }}
                >
                  <div className="flex items-start gap-3">
                    <span className="w-5 h-5 rounded-full bg-surface-tertiary flex items-center justify-center flex-shrink-0 text-2xs font-semibold text-text-tertiary mt-0.5">
                      {i + 1}
                    </span>
                    <div className="flex-1">
                      <p className="text-2xs uppercase tracking-wider text-text-tertiary font-medium mb-1">
                        {categoryLabel}{q.target_skill ? ` · ${q.target_skill}` : ""}
                      </p>
                      <p className="text-sm font-medium text-text-primary leading-relaxed">{q.question}</p>
                      <p className="text-xs text-text-tertiary mt-1.5 leading-relaxed">{q.rationale}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-text-tertiary py-3">
            Interview questions will be generated during analysis.
          </p>
        )}
      </div>
    </div>
  );
}
