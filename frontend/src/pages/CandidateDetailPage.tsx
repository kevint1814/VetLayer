import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft, Mail, Phone, MapPin, GraduationCap,
  Clock, FileText, Play, Loader2, AlertCircle, Trash2,
  Sparkles, CheckCircle2, Brain, Target, Zap, PartyPopper,
  Briefcase, Code, Award, FolderOpen, TrendingUp, AlertTriangle,
  ChevronDown, ChevronUp, MessageSquare, Compass, Users, Download,
} from "lucide-react";
import { candidatesApi, analysisApi, jobsApi } from "@/services/api";
import type { Candidate, Job, AnalysisResult, ParsedResume, IntelligenceProfile } from "@/types";
import ScoreBadge from "@/components/common/ScoreBadge";
import RecommendationBadge from "@/components/common/RecommendationBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";

const PIPELINE_STAGES = [
  { label: "Extracting skills", detail: "Parsing resume data...", icon: Brain },
  { label: "Mapping evidence", detail: "Finding skill proof points...", icon: Target },
  { label: "Estimating depth", detail: "Rating proficiency levels...", icon: Sparkles },
  { label: "Scoring & summary", detail: "Computing final scores...", icon: Zap },
];

type PipelinePhase = "idle" | "running" | "completing" | "success" | "done";

export default function CandidateDetailPage() {
  const { id } = useParams();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisResult[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [loading, setLoading] = useState(true);

  // Pipeline state
  const [pipelinePhase, setPipelinePhase] = useState<PipelinePhase>("idle");
  const [completedStages, setCompletedStages] = useState<number>(0); // 0..4
  const [pendingResult, setPendingResult] = useState<AnalysisResult | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Analysis delete
  const [deleteAnalysisId, setDeleteAnalysisId] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // PDF export with progress
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportStage, setExportStage] = useState("");
  const exportProgressRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const EXPORT_STAGES = [
    { at: 5, label: "Preparing candidate data..." },
    { at: 15, label: "Loading intelligence profile..." },
    { at: 30, label: "Composing executive summary..." },
    { at: 45, label: "Rendering skills assessment..." },
    { at: 55, label: "Building career timeline..." },
    { at: 70, label: "Formatting document layout..." },
    { at: 85, label: "Generating PDF..." },
    { at: 95, label: "Finalizing brief..." },
  ];

  const handleExportBrief = useCallback(async () => {
    if (!id || exporting) return;
    setExporting(true);
    setExportProgress(0);
    setExportStage("Initializing export...");

    // Animate progress in parallel with the actual API call
    let currentProg = 0;
    exportProgressRef.current = setInterval(() => {
      currentProg += 1;
      if (currentProg > 92) return; // Don't exceed 92% until real response
      setExportProgress(currentProg);
      const stage = EXPORT_STAGES.filter(s => s.at <= currentProg).pop();
      if (stage) setExportStage(stage.label);
    }, 80);

    try {
      const res = await candidatesApi.exportIntelligenceBrief(id);
      // Jump to 100%
      if (exportProgressRef.current) clearInterval(exportProgressRef.current);
      setExportProgress(100);
      setExportStage("Download ready!");
      const blob = new Blob([res.data], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Intelligence_Brief_${candidate?.name?.replace(/\s+/g, "_") || "Candidate"}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      // Brief pause to show completion
      await new Promise(r => setTimeout(r, 600));
    } catch (err) {
      console.error("PDF export failed:", err);
      if (exportProgressRef.current) clearInterval(exportProgressRef.current);
      setExportStage("Export failed. Please try again.");
      await new Promise(r => setTimeout(r, 1500));
    } finally {
      if (exportProgressRef.current) clearInterval(exportProgressRef.current);
      setExporting(false);
      setExportProgress(0);
      setExportStage("");
    }
  }, [id, exporting, candidate?.name]);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      candidatesApi.get(id).then((r) => setCandidate(r.data)),
      analysisApi.forCandidate(id).then((r) => setAnalyses(r.data.analyses || [])).catch(() => setAnalyses([])),
      jobsApi.list().then((r) => { setJobs(r.data.jobs); if (r.data.jobs.length) setSelectedJob(r.data.jobs[0].id); }),
    ]).finally(() => setLoading(false));
  }, [id]);

  // Elapsed timer during running phase
  useEffect(() => {
    if (pipelinePhase !== "running" && pipelinePhase !== "completing") {
      return;
    }
    const interval = setInterval(() => setElapsed((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, [pipelinePhase]);

  // Stage auto-advance during "running" phase (simulate progress based on time)
  useEffect(() => {
    if (pipelinePhase !== "running") return;

    // Advance stages based on elapsed time, but cap at stage 2 (don't fake completion)
    const maxStageWhileRunning = 2; // can show up to "Mapping evidence" done
    const thresholds = [8, 20]; // seconds to mark stage 0 and 1 as done
    let newCompleted = 0;
    for (let i = 0; i < thresholds.length; i++) {
      if (elapsed >= (thresholds[i] ?? Infinity)) newCompleted = i + 1;
    }
    setCompletedStages(Math.min(newCompleted, maxStageWhileRunning));
  }, [elapsed, pipelinePhase]);

  // Completing sequence: rapidly advance remaining stages
  useEffect(() => {
    if (pipelinePhase !== "completing") return;

    // Start from current completedStages and advance one every 400ms
    const advance = () => {
      setCompletedStages((prev) => {
        const next = prev + 1;
        if (next >= PIPELINE_STAGES.length) {
          // All stages done → go to success
          if (timerRef.current) clearInterval(timerRef.current);
          timerRef.current = null;
          setTimeout(() => setPipelinePhase("success"), 300);
          return PIPELINE_STAGES.length;
        }
        return next;
      });
    };

    // Immediately advance one stage, then continue
    advance();
    timerRef.current = setInterval(advance, 450);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [pipelinePhase]);

  // Cleanup export progress interval on unmount (prevent leak if user navigates mid-export)
  useEffect(() => {
    return () => {
      if (exportProgressRef.current) {
        clearInterval(exportProgressRef.current);
        exportProgressRef.current = null;
      }
    };
  }, []);

  // Success phase: show celebration briefly, then reveal result
  useEffect(() => {
    if (pipelinePhase !== "success") return;

    const timer = setTimeout(() => {
      if (pendingResult) {
        setAnalyses((prev) => [pendingResult, ...prev]);
        setHighlightId(pendingResult.id);
        setTimeout(() => setHighlightId(null), 3000);
      }
      setPipelinePhase("done");
      // Reset after animation
      setTimeout(() => {
        setPipelinePhase("idle");
        setPendingResult(null);
        setCompletedStages(0);
        setElapsed(0);
      }, 500);
    }, 1200);

    return () => clearTimeout(timer);
  }, [pipelinePhase, pendingResult]);

  const runAnalysis = useCallback(async () => {
    if (!id || !selectedJob) return;
    setPipelinePhase("running");
    setCompletedStages(0);
    setElapsed(0);
    setError(null);
    setPendingResult(null);

    try {
      const res = await analysisApi.trigger(id, selectedJob);
      const full = await analysisApi.get(res.data.analysis_id);
      // Store result and start completion sequence
      setPendingResult(full.data);
      setPipelinePhase("completing");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Analysis failed.";
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || msg);
      console.error("Analysis error:", err);
      setPipelinePhase("idle");
      setCompletedStages(0);
      setElapsed(0);
    }
  }, [id, selectedJob]);

  const handleDeleteAnalysis = async () => {
    if (!deleteAnalysisId) return;
    setDeleteLoading(true);
    try {
      await analysisApi.delete(deleteAnalysisId);
      setAnalyses((prev) => prev.filter((a) => a.id !== deleteAnalysisId));
    } catch (err) {
      console.error(err);
      setError("Failed to delete analysis.");
    } finally {
      setDeleteLoading(false);
      setDeleteAnalysisId(null);
    }
  };

  if (loading) {
    return (
      <div className="page-container">
        <div className="h-4 w-32 shimmer rounded-lg mb-6" />
        <div className="glass-card-solid p-6 mb-5">
          <div className="flex items-start gap-6">
            <div className="w-14 h-14 shimmer rounded-xl" />
            <div className="flex-1 space-y-3">
              <div className="h-6 w-48 shimmer rounded-lg" />
              <div className="h-4 w-72 shimmer rounded-lg" />
              <div className="flex gap-4 mt-4">
                {[1,2,3].map(i => <div key={i} className="h-4 w-24 shimmer rounded-lg" />)}
              </div>
            </div>
          </div>
        </div>
        <div className="glass-card-solid p-5 shimmer h-20 mb-5" />
      </div>
    );
  }

  if (!candidate) {
    return (
      <div className="p-10 text-center">
        <div className="w-14 h-14 rounded-xl bg-surface-tertiary flex items-center justify-center mx-auto mb-4">
          <AlertCircle size={28} className="text-text-tertiary" />
        </div>
        <p className="text-text-secondary">Candidate not found.</p>
        <Link to="/candidates" className="text-sm text-brand-500 hover:text-brand-600 mt-2 inline-block">
          Back to candidates
        </Link>
      </div>
    );
  }

  const isAnalyzing = pipelinePhase === "running" || pipelinePhase === "completing" || pipelinePhase === "success";

  // Figure out which stage is "active" (the one currently being worked on)
  // completedStages = number of stages fully done (0..4)
  const activeStageIndex = completedStages < PIPELINE_STAGES.length ? completedStages : -1;

  // Progress bar percentage
  const progressPercent = pipelinePhase === "success"
    ? 100
    : pipelinePhase === "completing"
    ? 70 + (completedStages / PIPELINE_STAGES.length) * 30
    : Math.min((elapsed / 90) * 70, 70); // cap at 70% while running

  return (
    <div className="page-container animate-fade-in">
      {/* ── Back ─────────────────────────────────────────────────── */}
      <Link to="/candidates" className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary mb-6 transition-colors">
        <ArrowLeft size={15} />
        Back to Candidates
      </Link>

      {/* ── Profile header ───────────────────────────────────────── */}
      <div className="glass-card-solid p-6 mb-5">
        <div className="flex items-start gap-6">
          <div className="w-14 h-14 rounded-xl bg-brand-50 flex items-center justify-center flex-shrink-0">
            <span className="text-2xl font-bold text-brand-500">
              {candidate.name.split(" ").map(n => n[0]).join("").slice(0, 2).toUpperCase()}
            </span>
          </div>
          <div className="flex-1">
            <h1 className="text-xl font-semibold text-text-primary">{candidate.name}</h1>
            {candidate.current_role && (
              <p className="text-sm text-text-secondary mt-0.5">
                {candidate.current_role}
                {candidate.current_company && <span className="text-text-tertiary"> at {candidate.current_company}</span>}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 mt-4">
              {candidate.email && (
                <span className="flex items-center gap-1.5 text-xs text-text-tertiary">
                  <Mail size={13} /> {candidate.email}
                </span>
              )}
              {candidate.phone && (
                <span className="flex items-center gap-1.5 text-xs text-text-tertiary">
                  <Phone size={13} /> {candidate.phone}
                </span>
              )}
              {candidate.location && (
                <span className="flex items-center gap-1.5 text-xs text-text-tertiary">
                  <MapPin size={13} /> {candidate.location}
                </span>
              )}
              {candidate.years_experience !== undefined && candidate.years_experience !== null && (
                <span className="flex items-center gap-1.5 text-xs text-text-tertiary">
                  <Clock size={13} /> {candidate.years_experience < 1 ? "<1" : Math.round(candidate.years_experience)} years experience
                </span>
              )}
              {candidate.education_level && (
                <span className="flex items-center gap-1.5 text-xs text-text-tertiary">
                  <GraduationCap size={13} /> {candidate.education_level}
                </span>
              )}
            </div>
          </div>
          <div className="flex-shrink-0 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs text-text-tertiary bg-surface-tertiary px-3 py-1.5 rounded-lg">
              <FileText size={13} /> {candidate.resume_filename}
            </span>
            {(candidate.resume_parsed || candidate.intelligence_profile) && (
              <button
                onClick={handleExportBrief}
                disabled={exporting}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-brand-600 bg-brand-50 hover:bg-brand-100 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
                title="Export Intelligence Brief as PDF"
              >
                {exporting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                {exporting ? "Exporting..." : "Export Brief"}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Export Progress Overlay ─────────────────────────────────── */}
      {exporting && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm animate-fade-in">
          <div className="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md mx-4">
            <div className="flex items-center gap-3 mb-5">
              <div className="w-10 h-10 rounded-xl bg-brand-50 flex items-center justify-center">
                <FileText size={20} className="text-brand-500" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Generating Intelligence Brief</h3>
                <p className="text-xs text-text-tertiary mt-0.5">{candidate.name}</p>
              </div>
            </div>
            <div className="w-full h-2 bg-surface-tertiary rounded-full overflow-hidden mb-3">
              <div
                className="h-full bg-brand-500 rounded-full transition-all duration-300 ease-out"
                style={{ width: `${exportProgress}%` }}
              />
            </div>
            <div className="flex items-center justify-between">
              <p className="text-xs text-text-secondary">{exportStage}</p>
              <span className="text-xs font-medium text-brand-600">{exportProgress}%</span>
            </div>
          </div>
        </div>
      )}

      {/* ── Candidate Intelligence Brief ──────────────────────────── */}
      {(candidate.resume_parsed || candidate.intelligence_profile) && (
        <ProfessionalDossier
          parsed={candidate.resume_parsed || {}}
          candidate={candidate}
          profile={candidate.intelligence_profile}
        />
      )}

      {/* ── Run analysis section ─────────────────────────────────── */}
      <div className="glass-card-solid p-5 mb-5 overflow-hidden">
        <h2 className="text-sm font-semibold text-text-primary mb-4">Run Analysis</h2>
        <div className="flex items-center gap-3">
          <select
            value={selectedJob}
            onChange={(e) => setSelectedJob(e.target.value)}
            disabled={isAnalyzing}
            className="input-field max-w-sm disabled:opacity-50"
          >
            {jobs.length === 0 && <option value="">No jobs available, create one first</option>}
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.title}{j.company ? ` (${j.company})` : ""}
              </option>
            ))}
          </select>
          <button
            onClick={runAnalysis}
            disabled={isAnalyzing || !selectedJob}
            className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed group"
          >
            {isAnalyzing ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Analyzing...
              </>
            ) : (
              <>
                <Play size={16} className="group-hover:scale-110 transition-transform" />
                Run Pipeline
              </>
            )}
          </button>
        </div>

        {/* ── Dynamic pipeline progress ─────────────────────────── */}
        {isAnalyzing && (
          <div className="mt-6 animate-slide-up">
            {/* Progress bar */}
            <div className="w-full bg-surface-tertiary rounded-full h-2 overflow-hidden mb-6">
              <div
                className={`h-full rounded-full transition-all ease-out ${
                  pipelinePhase === "success"
                    ? "bg-emerald-500 duration-300"
                    : "bg-brand-500 progress-stripe duration-1000"
                }`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>

            {/* Success banner */}
            {pipelinePhase === "success" && (
              <div className="flex items-center gap-3 p-4 bg-emerald-50 rounded-xl border border-emerald-200 mb-5 animate-scale-in">
                <div className="w-10 h-10 rounded-xl bg-emerald-100 flex items-center justify-center">
                  <PartyPopper size={20} className="text-emerald-600" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-emerald-800">Analysis complete!</p>
                  <p className="text-xs text-emerald-600">
                    Finished in {elapsed}s
                    {pendingResult && ` — Score: ${Math.round(pendingResult.overall_score * 100)}`}
                  </p>
                </div>
                {pendingResult && (
                  <div className="ml-auto">
                    <RecommendationBadge recommendation={pendingResult.recommendation || "pending"} size="sm" />
                  </div>
                )}
              </div>
            )}

            {/* Pipeline stages */}
            <div className="relative">
              {/* Connector line */}
              <div className="absolute top-5 left-5 right-5 h-[2px] bg-surface-tertiary z-0" />
              <div
                className="absolute top-5 left-5 h-[2px] bg-brand-500 z-[1] transition-all duration-500 ease-out"
                style={{ width: `${Math.max(0, ((completedStages) / (PIPELINE_STAGES.length - 1)) * (100 - 10))}%` }}
              />

              <div className="flex items-start justify-between relative z-10">
                {PIPELINE_STAGES.map((stage, i) => {
                  const isDone = i < completedStages;
                  const isActive = i === activeStageIndex && pipelinePhase !== "success";
                  const Icon = stage.icon;

                  return (
                    <div key={i} className="flex flex-col items-center text-center flex-1">
                      <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-2 transition-all duration-500 ${
                        isDone
                          ? "bg-emerald-500 text-white scale-100"
                          : isActive
                          ? "bg-brand-500 text-white ring-4 ring-brand-100 scale-110"
                          : "bg-white text-text-tertiary border-2 border-surface-border"
                      }`}>
                        {isDone ? (
                          <CheckCircle2 size={18} className="animate-scale-in" />
                        ) : isActive ? (
                          <Icon size={18} className="animate-pulse" />
                        ) : (
                          <Icon size={18} />
                        )}
                      </div>
                      <p className={`text-xs font-medium transition-colors duration-300 ${
                        isDone ? "text-emerald-600" : isActive ? "text-brand-600" : "text-text-tertiary"
                      }`}>
                        {stage.label}
                      </p>
                      {isActive && (
                        <p className="text-2xs text-brand-400 mt-1 animate-fade-in">{stage.detail}</p>
                      )}
                      {isActive && (
                        <p className="text-2xs text-text-tertiary mt-0.5">{elapsed}s</p>
                      )}
                      {isDone && i === completedStages - 1 && pipelinePhase === "completing" && (
                        <p className="text-2xs text-emerald-500 mt-1 animate-fade-in">Done</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* Error feedback */}
        {error && (
          <div className="mt-4 p-4 bg-red-50 rounded-xl border border-red-100 animate-slide-up">
            <div className="flex items-center gap-3">
              <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
              <div>
                <p className="text-sm font-medium text-red-800">Analysis failed</p>
                <p className="text-xs text-red-600 mt-0.5">{error}</p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Delete analysis confirm ─────────────────────────────── */}
      <ConfirmDialog
        open={!!deleteAnalysisId}
        title="Delete analysis?"
        description="This will permanently remove this analysis, including all risk flags and interview questions. This cannot be undone."
        actionText="Delete"
        danger
        loading={deleteLoading}
        onConfirm={handleDeleteAnalysis}
        onCancel={() => setDeleteAnalysisId(null)}
      />

      {/* ── Analysis results ─────────────────────────────────────── */}
      {analyses.length > 0 ? (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold text-text-primary">
            Analysis Results
            <span className="text-xs text-text-tertiary font-normal ml-2">
              ({analyses.length})
            </span>
          </h2>
          {analyses.map((a, i) => (
            <div
              key={a.id}
              className={`glass-card-solid p-6 transition-all duration-500 group hover-lift ${
                highlightId === a.id
                  ? "ring-2 ring-emerald-300 bg-emerald-50/30 shadow-glass animate-slide-up"
                  : "hover:shadow-glass stagger-item"
              }`}
              style={highlightId !== a.id ? { animationDelay: `${i * 80}ms` } : undefined}
            >
              <Link to={`/analysis/${a.id}`} className="block">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <ScoreBadge score={a.overall_score} />
                    {a.recommendation && (
                      <RecommendationBadge recommendation={a.recommendation} size="sm" />
                    )}
                    {highlightId === a.id && (
                      <span className="text-2xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium animate-fade-in">
                        Just analyzed
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-text-tertiary">
                    <span className="bg-surface-tertiary px-2.5 py-1 rounded-lg">
                      VetLayer Engine
                    </span>
                    {a.processing_time_ms && (
                      <span className="flex items-center gap-1">
                        <Clock size={11} />
                        {(a.processing_time_ms / 1000).toFixed(1)}s
                      </span>
                    )}
                    <span>
                      {new Date(a.created_at).toLocaleDateString("en-US", {
                        month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
                      })}
                    </span>
                  </div>
                </div>
                {a.summary_text && (
                  <p className="text-sm text-text-secondary mt-3 leading-relaxed line-clamp-2">
                    {a.summary_text}
                  </p>
                )}
              </Link>
              <div className="flex justify-end mt-3 pt-3 border-t border-surface-border">
                <button
                  onClick={() => setDeleteAnalysisId(a.id)}
                  className="opacity-0 group-hover:opacity-100 inline-flex items-center gap-1.5 text-xs text-text-tertiary hover:text-red-500 transition-all"
                >
                  <Trash2 size={12} />
                  Delete analysis
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : !isAnalyzing ? (
        <div className="glass-card-solid p-10 text-center">
          <div className="w-12 h-12 rounded-xl bg-surface-tertiary flex items-center justify-center mx-auto mb-4">
            <Sparkles size={24} className="text-text-tertiary" />
          </div>
          <p className="text-base font-medium text-text-primary">No analyses yet</p>
          <p className="text-sm text-text-tertiary mt-1.5">
            Select a job above and run the analysis pipeline to evaluate this candidate.
          </p>
        </div>
      ) : null}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// CANDIDATE INTELLIGENCE BRIEF
// ════════════════════════════════════════════════════════════════════

/** Ensure a URL has a protocol so <a href> doesn't treat it as a relative path. */
const ensureUrl = (url: string) =>
  /^https?:\/\//i.test(url) ? url : `https://${url}`;

// Category color map for skill chips
const CATEGORY_COLORS: Record<string, string> = {
  "Languages": "bg-blue-50 text-blue-600",
  "Frameworks": "bg-violet-50 text-violet-600",
  "Data & ML": "bg-emerald-50 text-emerald-600",
  "Data/ML": "bg-emerald-50 text-emerald-600",
  "Cloud & DevOps": "bg-orange-50 text-orange-600",
  "Cloud/DevOps": "bg-orange-50 text-orange-600",
  "Databases": "bg-cyan-50 text-cyan-600",
  "Tools": "bg-slate-50 text-slate-600",
  "Design": "bg-pink-50 text-pink-600",
  "Soft Skills": "bg-rose-50 text-rose-600",
};

interface DossierProps {
  parsed: ParsedResume;
  candidate: Candidate;
  profile?: IntelligenceProfile;
}

/**
 * Condense raw bullet-point descriptions into a recruiter-friendly summary.
 * Takes the first N meaningful lines, strips bullet markers, joins cleanly.
 */
function condenseBullets(text: string, maxLines = 3): { condensed: string; isTruncated: boolean } {
  if (!text) return { condensed: "", isTruncated: false };
  // Split on newlines and bullet markers
  const lines = text
    .split(/\n|•|·/)
    .map(l => l.replace(/^[\s\-–—*]+/, "").trim())
    .filter(l => l.length > 15); // Skip very short fragments
  if (lines.length <= maxLines) {
    return { condensed: lines.map(l => `• ${l}`).join("\n"), isTruncated: false };
  }
  return {
    condensed: lines.slice(0, maxLines).map(l => `• ${l}`).join("\n"),
    isTruncated: true,
  };
}

/** Renders text with truncation + "Show more" toggle */
function TruncatedDescription({ text, maxLines = 3 }: { text: string; maxLines?: number }) {
  const [expanded, setExpanded] = useState(false);
  const { condensed, isTruncated } = condenseBullets(text, maxLines);

  if (!text) return null;

  return (
    <div>
      <p className="text-xs text-text-tertiary mt-1.5 leading-relaxed whitespace-pre-line">
        {expanded ? text : condensed}
      </p>
      {isTruncated && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-2xs text-brand-500 hover:text-brand-600 font-medium mt-1 transition-colors"
        >
          {expanded ? "Show less" : "Show full details"}
        </button>
      )}
    </div>
  );
}

function ProfessionalDossier({ parsed, candidate, profile }: DossierProps) {
  const [timelineExpanded, setTimelineExpanded] = useState(false);

  const p = profile; // shorthand
  const hasExperience = parsed.experience && parsed.experience.length > 0;

  // Deduplicate education entries (same degree+institution combo)
  const dedupeEducation = (eduList: any[]) => {
    const seen = new Set<string>();
    return eduList.filter(edu => {
      const degree = (edu.degree || "").trim().toLowerCase();
      const institution = (edu.institution || "").trim().toLowerCase();
      const key = institution ? `${degree}|${institution}` : degree;
      if (key && seen.has(key)) return false;
      // Normalize class variants (Class X / Class 10 / SSLC, Class XII / Class 12 / HSC)
      const degreeNorm = degree.replace("class ", "").replace("10th", "x").replace(/^10$/, "x").replace("12th", "xii").replace(/^12$/, "xii").replace("sslc", "x").replace("hsc", "xii");
      const altKey = `${degreeNorm}|norm`;
      if (degreeNorm && seen.has(altKey)) return false;
      seen.add(key);
      if (degreeNorm) seen.add(altKey);
      return true;
    });
  };
  const dedupedEducation = parsed.education ? dedupeEducation(parsed.education) : [];
  const hasEducation = dedupedEducation.length > 0;
  const hasCertifications = parsed.certifications && parsed.certifications.length > 0;
  const hasProjects = parsed.projects && parsed.projects.length > 0;
  const hasSkillCategories = p?.skill_categories && Object.keys(p.skill_categories).length > 0;
  const hasSkills = hasSkillCategories || (parsed.skills_mentioned && parsed.skills_mentioned.length > 0);

  const hasAnyContent = p?.executive_summary || parsed.summary || hasExperience || hasEducation || hasSkills;
  if (!hasAnyContent) return null;

  const visibleExperience = hasExperience
    ? (timelineExpanded ? parsed.experience! : parsed.experience!.slice(0, 3))
    : [];

  // ── Section helper ────────────────────────────────────────────
  const SectionHeader = ({ icon: Icon, title, badge }: {
    icon: React.ComponentType<{ size?: number; className?: string }>;
    title: string;
    badge?: string;
  }) => (
    <div className="flex items-center gap-2 mb-4">
      <Icon size={14} className="text-text-tertiary" />
      <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">{title}</h3>
      {badge && <span className="text-2xs text-text-tertiary ml-auto">{badge}</span>}
    </div>
  );

  return (
    <div className="space-y-4 mb-5 animate-fade-in">
      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 1. EXECUTIVE SUMMARY                                      */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <div className="glass-card-solid overflow-hidden">
        <div className="bg-gradient-to-r from-brand-50 via-brand-50/30 to-transparent p-6">
          <div className="flex items-center gap-2 mb-3">
            <Briefcase size={15} className="text-brand-500" />
            <h2 className="text-sm font-semibold text-text-primary">Candidate Intelligence Brief</h2>
          </div>
          <p className="text-sm text-text-secondary leading-relaxed">
            {p?.executive_summary || parsed.summary || (
              `${candidate.name} is ${candidate.current_role ? `a ${candidate.current_role}` : "a professional"}${
                candidate.current_company ? ` at ${candidate.current_company}` : ""
              }${candidate.years_experience ? ` with ${Math.round(candidate.years_experience)} years of experience` : ""}.`
            )}
          </p>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 2. KEY METRICS                                            */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { icon: Clock, label: "Experience", value: candidate.years_experience !== undefined && candidate.years_experience !== null ? (candidate.years_experience < 1 ? "<1 year" : `${Math.round(candidate.years_experience)} years`) : "N/A" },
          { icon: Code, label: "Skills", value: parsed.skills_mentioned ? `${parsed.skills_mentioned.length} identified` : "N/A" },
          { icon: GraduationCap, label: "Education", value: candidate.education_level || "N/A" },
          { icon: TrendingUp, label: "Seniority", value: p?.seniority_level || "N/A" },
        ].map((m, i) => (
          <div key={i} className="bg-surface-secondary rounded-xl p-4 flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-white flex items-center justify-center flex-shrink-0 shadow-sm">
              <m.icon size={16} className="text-brand-500" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-2xs text-text-tertiary uppercase tracking-wider">{m.label}</p>
              <p className="text-sm font-semibold text-text-primary truncate">{m.value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 3. CAREER NARRATIVE + STRENGTHS                           */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Career Narrative */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-6 h-6 rounded-md bg-brand-50 flex items-center justify-center">
              <Compass size={13} className="text-brand-500" />
            </div>
            <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Career Narrative</h3>
          </div>
          {p?.career_narrative ? (
            <p className="text-sm text-text-secondary leading-relaxed">{p.career_narrative}</p>
          ) : (
            <p className="text-sm text-text-tertiary leading-relaxed">
              {candidate.current_role ? `Currently serving as ${candidate.current_role}` : "Professional"}
              {candidate.current_company ? ` at ${candidate.current_company}` : ""}
              {candidate.years_experience ? ` with ${Math.round(candidate.years_experience)} years of experience.` : "."}
            </p>
          )}
        </div>

        {/* Key Strengths */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-6 h-6 rounded-md bg-emerald-100 flex items-center justify-center">
              <TrendingUp size={13} className="text-emerald-600" />
            </div>
            <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Key Strengths</h3>
          </div>
          <div className="space-y-2">
            {(p?.strengths && p.strengths.length > 0) ? p.strengths.map((s, i) => (
              <div key={i} className="flex items-start gap-2">
                <CheckCircle2 size={14} className="text-emerald-500 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-text-secondary leading-relaxed">{s}</p>
              </div>
            )) : (
              <p className="text-sm text-text-tertiary">Re-upload resume to generate detailed strengths analysis</p>
            )}
          </div>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 4. SKILLS PROFILE                                         */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {hasSkills && (
        <div className="glass-card-solid p-5">
          <SectionHeader icon={Code} title="Skills Profile" badge={`${parsed.skills_mentioned?.length || 0} skills`} />

          {/* AI skill narrative */}
          {p?.skill_narrative && (
            <p className="text-sm text-text-secondary leading-relaxed mb-4 pb-4 border-b border-surface-border">
              {p.skill_narrative}
            </p>
          )}

          {/* Categorized skills — prefer AI categories, fallback to raw list */}
          {hasSkillCategories ? (
            <div className="space-y-4">
              {Object.entries(p!.skill_categories!).map(([category, skills]) => (
                <div key={category}>
                  <p className="text-2xs font-semibold text-text-tertiary uppercase tracking-wider mb-2">{category}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {skills.map((skill, i) => (
                      <span key={i} className={`text-xs px-2.5 py-1 rounded-lg font-medium ${
                        CATEGORY_COLORS[category] || "bg-surface-tertiary text-text-secondary"
                      }`}>
                        {skill}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : parsed.skills_mentioned && (
            <div className="flex flex-wrap gap-1.5">
              {parsed.skills_mentioned.map((skill, i) => (
                <span key={i} className="text-xs bg-brand-50 text-brand-600 px-2.5 py-1 rounded-lg font-medium">
                  {skill}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 5. IDEAL ROLES + CULTURE SIGNALS (AI only)                */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {p && (p.ideal_roles?.length || p.culture_signals) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {p.ideal_roles && p.ideal_roles.length > 0 && (
            <div className="glass-card-solid p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-6 h-6 rounded-md bg-violet-100 flex items-center justify-center">
                  <Compass size={13} className="text-violet-600" />
                </div>
                <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Ideal Roles</h3>
              </div>
              <div className="space-y-2">
                {p.ideal_roles.map((role, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-violet-400 mt-1.5 flex-shrink-0" />
                    <p className="text-sm text-text-secondary leading-relaxed">{role}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {p.culture_signals && (
            <div className="glass-card-solid p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-6 h-6 rounded-md bg-blue-100 flex items-center justify-center">
                  <Users size={13} className="text-blue-600" />
                </div>
                <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Culture Signals</h3>
              </div>
              <p className="text-sm text-text-secondary leading-relaxed">{p.culture_signals}</p>
            </div>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 6. CAREER TIMELINE                                        */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {hasExperience && (
        <div className="glass-card-solid p-5">
          <SectionHeader icon={Clock} title="Career Timeline" badge={`${parsed.experience!.length} roles`} />
          <div className="space-y-0">
            {visibleExperience.map((exp, i) => (
              <div key={i} className="flex gap-3 relative">
                <div className="flex flex-col items-center flex-shrink-0">
                  <div className={`w-2.5 h-2.5 rounded-full mt-1.5 flex-shrink-0 z-10 ${
                    i === 0 ? "bg-brand-500 ring-2 ring-brand-100" : "bg-brand-300"
                  }`} />
                  {i < visibleExperience.length - 1 && (
                    <div className="w-px flex-1 bg-surface-border" />
                  )}
                </div>
                <div className="pb-4 flex-1 min-w-0">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <p className="text-sm font-medium text-text-primary">{exp.title || "Untitled Role"}</p>
                    {exp.company && <span className="text-xs text-text-secondary">at {exp.company}</span>}
                  </div>
                  {(exp.start_date || exp.end_date) && (
                    <p className="text-2xs text-text-tertiary mt-0.5">
                      {exp.start_date || "?"} — {exp.end_date || "Present"}
                    </p>
                  )}
                  {exp.description && (
                    <TruncatedDescription text={exp.description} maxLines={3} />
                  )}
                  {exp.technologies && exp.technologies.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {exp.technologies.slice(0, 6).map((tech, j) => (
                        <span key={j} className="text-2xs bg-surface-tertiary text-text-tertiary px-1.5 py-0.5 rounded">
                          {tech}
                        </span>
                      ))}
                      {exp.technologies.length > 6 && (
                        <span className="text-2xs text-text-tertiary">+{exp.technologies.length - 6}</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          {parsed.experience!.length > 3 && (
            <button
              onClick={() => setTimelineExpanded(!timelineExpanded)}
              className="flex items-center gap-1.5 text-xs text-brand-500 hover:text-brand-600 font-medium mt-2 transition-colors"
            >
              {timelineExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              {timelineExpanded ? "Show less" : `Show all ${parsed.experience!.length} roles`}
            </button>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 7. EDUCATION & CREDENTIALS                                */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {(hasEducation || hasCertifications) && (
        <div className="glass-card-solid p-5">
          <SectionHeader icon={GraduationCap} title="Education & Credentials" />
          {hasEducation && (
            <div className="space-y-3 mb-4">
              {dedupedEducation.map((edu, i) => (
                <div key={i} className="flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <GraduationCap size={14} className="text-blue-500" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-text-primary">
                      {[edu.degree, edu.field].filter(Boolean).join(" in ") || "Degree"}
                    </p>
                    {edu.institution && <p className="text-xs text-text-secondary">{edu.institution}</p>}
                    {(edu.graduation_date || edu.gpa) && (
                      <p className="text-2xs text-text-tertiary mt-0.5">
                        {[edu.graduation_date, edu.gpa ? `GPA: ${edu.gpa}` : null].filter(Boolean).join(" · ")}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {hasCertifications && (
            <>
              {hasEducation && <div className="border-t border-surface-border pt-4 mt-2" />}
              <div className="flex items-center gap-2 mb-3">
                <Award size={13} className="text-amber-500" />
                <span className="text-2xs font-semibold text-text-tertiary uppercase tracking-wider">Certifications</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {parsed.certifications!.map((cert, i) => {
                  const label = typeof cert === "string" ? cert : cert.name || "Certification";
                  const detail = typeof cert === "object" ? [cert.issuer, cert.date].filter(Boolean).join(" · ") : null;
                  return (
                    <span key={i} className="text-xs bg-amber-50 text-amber-700 px-2.5 py-1 rounded-lg font-medium">
                      {label}{detail && <span className="text-amber-500 font-normal"> — {detail}</span>}
                    </span>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 8. CONSIDERATIONS (AI only)                               */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {p?.considerations && p.considerations.length > 0 && (
        <div className="glass-card-solid p-5 border-l-4 border-amber-300">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-6 h-6 rounded-md bg-amber-50 flex items-center justify-center">
              <AlertTriangle size={13} className="text-amber-500" />
            </div>
            <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Considerations</h3>
          </div>
          <div className="space-y-2">
            {p.considerations.map((c, i) => (
              <div key={i} className="flex items-start gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-amber-400 mt-1.5 flex-shrink-0" />
                <p className="text-sm text-text-secondary leading-relaxed">{c}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 9. TALKING POINTS (AI only)                               */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {p?.talking_points && p.talking_points.length > 0 && (
        <div className="glass-card-solid p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-6 h-6 rounded-md bg-brand-50 flex items-center justify-center">
              <MessageSquare size={13} className="text-brand-500" />
            </div>
            <h3 className="text-xs font-semibold text-text-primary uppercase tracking-wider">Recruiter Talking Points</h3>
          </div>
          <div className="space-y-2.5">
            {p.talking_points.map((tp, i) => (
              <div key={i} className="flex items-start gap-2.5">
                <div className="w-5 h-5 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <span className="text-2xs font-bold text-brand-500">{i + 1}</span>
                </div>
                <p className="text-sm text-text-secondary leading-relaxed">{tp}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 10. PROJECTS & PORTFOLIO                                  */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {hasProjects && (
        <div className="glass-card-solid p-5">
          <SectionHeader icon={FolderOpen} title="Projects & Portfolio" badge={`${parsed.projects!.length} projects`} />
          <div className="space-y-4">
            {parsed.projects!.map((proj, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <FolderOpen size={14} className="text-purple-500" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium text-text-primary">{proj.name || "Unnamed Project"}</p>
                    {proj.url && (
                      <a href={ensureUrl(proj.url)} target="_blank" rel="noopener noreferrer"
                        className="text-2xs text-brand-500 hover:text-brand-600 hover:underline transition-colors">
                        View Project &rarr;
                      </a>
                    )}
                  </div>
                  {proj.description && (
                    <TruncatedDescription text={proj.description} maxLines={2} />
                  )}
                  {proj.technologies && proj.technologies.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {proj.technologies.map((tech, j) => (
                        <span key={j} className="text-2xs bg-surface-tertiary text-text-tertiary px-1.5 py-0.5 rounded">
                          {tech}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* 11. LINKS & PROFILES                                      */}
      {/* ═══════════════════════════════════════════════════════════ */}
      {parsed.links && parsed.links.length > 0 && (
        <div className="glass-card-solid p-5">
          <SectionHeader icon={Compass} title="Links & Profiles" />
          <div className="flex flex-wrap gap-2">
            {parsed.links.map((link, i) => {
              const url = ensureUrl(typeof link === "string" ? link : link.url);
              const label = typeof link === "string"
                ? (url.includes("linkedin") ? "LinkedIn" : url.includes("github") ? "GitHub" : url.includes("twitter") || url.includes("x.com") ? "Twitter/X" : "Website")
                : link.label || "Link";
              const iconColor = label === "LinkedIn" ? "text-blue-600 bg-blue-50" : label === "GitHub" ? "text-gray-800 bg-gray-100" : "text-brand-500 bg-brand-50";
              return (
                <a key={i} href={url} target="_blank" rel="noopener noreferrer"
                  className={`flex items-center gap-2 text-xs font-medium px-3 py-2 rounded-lg hover:opacity-80 transition-opacity ${iconColor}`}>
                  <Compass size={13} />
                  {label}
                  <span className="text-2xs opacity-60">&rarr;</span>
                </a>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
