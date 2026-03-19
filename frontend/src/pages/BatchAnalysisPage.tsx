import { useEffect, useState, useCallback, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Zap, Users, Briefcase, ChevronRight, Loader2, CheckCircle2,
  AlertCircle, Trophy, XCircle, Clock, Trash2, ArrowLeft, PartyPopper, BarChart3, Download
} from "lucide-react";
import { candidatesApi, jobsApi, analysisApi } from "@/services/api";
import type { Candidate, Job, BatchAnalysisStatus } from "@/types";
import RecommendationBadge from "@/components/common/RecommendationBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";

type CompletionPhase = "none" | "celebrating" | "revealed";

export default function BatchAnalysisPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  // Selection
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(new Set());
  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [forceReanalyze, setForceReanalyze] = useState(false);

  // Batch status (active batch)
  const [batchStatus, setBatchStatus] = useState<BatchAnalysisStatus | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Completion animation state
  const [completionPhase, setCompletionPhase] = useState<CompletionPhase>("none");
  const prevStatusRef = useRef<string | null>(null);

  // Past batches
  const [savedBatches, setSavedBatches] = useState<BatchAnalysisStatus[]>([]);
  const [loadingSaved, setLoadingSaved] = useState(true);

  // Delete confirmation
  const [deleteBatchId, setDeleteBatchId] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  // Live timer (client-side, independent of polling)
  const [liveElapsed, setLiveElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const batchStartRef = useRef<number | null>(null);

  // Start/stop live timer based on batch status
  useEffect(() => {
    if (batchStatus?.status === "processing") {
      if (!batchStartRef.current) batchStartRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setLiveElapsed(Date.now() - (batchStartRef.current || Date.now()));
      }, 200);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = null;
      // When done, use the server's authoritative elapsed time
      if (batchStatus?.elapsed_ms) setLiveElapsed(batchStatus.elapsed_ms);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [batchStatus?.status]);

  // Reset timer when starting a new batch
  useEffect(() => {
    if (launching) {
      batchStartRef.current = Date.now();
      setLiveElapsed(0);
    }
  }, [launching]);

  const formatElapsed = (ms: number) => {
    if (ms < 1000) return "0s";
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const mins = Math.floor(ms / 60000);
    const secs = Math.floor((ms % 60000) / 1000);
    return `${mins}m ${secs}s`;
  };

  // Export state
  const [exporting, setExporting] = useState(false);

  const [exportingJobId, setExportingJobId] = useState<string | null>(null);

  const handleExportBrief = useCallback(async (batchId: string, jobId: string) => {
    setExporting(true);
    setExportingJobId(jobId);
    try {
      const response = await analysisApi.exportBatchBrief(batchId, jobId);
      const blob = new Blob([response.data], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const disposition = response.headers["content-disposition"];
      const filenameMatch = disposition?.match(/filename="?([^"]+)"?/);
      a.download = filenameMatch ? filenameMatch[1] : `Batch_Analysis_Brief.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      console.error("Export failed:", err);
      setError(err?.response?.data?.detail || "Failed to export batch brief");
    } finally {
      setExporting(false);
      setExportingJobId(null);
    }
  }, []);

  // Load candidates + jobs + saved batches
  useEffect(() => {
    Promise.all([
      candidatesApi.list({ limit: 200 }).then((r) => setCandidates(r.data.candidates || [])),
      jobsApi.list({ limit: 100 }).then((r) => setJobs(r.data.jobs || [])),
    ]).finally(() => setLoading(false));

    analysisApi.listBatches().then((r) => {
      setSavedBatches(r.data || []);
    }).finally(() => setLoadingSaved(false));
  }, []);

  // If we have a detail ID in URL, load that batch
  const detailId = searchParams.get("detail");
  useEffect(() => {
    if (detailId && !batchStatus) {
      analysisApi.getBatchProgress(detailId).then((r) => {
        setBatchStatus(r.data);
        // If it's still processing, start polling
        if (r.data.status === "processing") {
          pollRef.current = setInterval(async () => {
            try {
              const progress = await analysisApi.getBatchProgress(detailId);
              setBatchStatus(progress.data);
              if (progress.data.status !== "processing") {
                if (pollRef.current) clearInterval(pollRef.current);
                pollRef.current = null;
              }
            } catch (err) {
              console.error("Poll error:", err);
            }
          }, 2000);
        }
      }).catch(() => {
        setSearchParams({});
      });
    }
  }, [detailId]);

  // Detect completion transition → trigger celebration
  useEffect(() => {
    if (!batchStatus) {
      prevStatusRef.current = null;
      return;
    }
    const prev = prevStatusRef.current;
    prevStatusRef.current = batchStatus.status;

    if (prev === "processing" && batchStatus.status !== "processing") {
      // Batch just finished — celebrate!
      setCompletionPhase("celebrating");
      setTimeout(() => setCompletionPhase("revealed"), 1800);
    }
  }, [batchStatus?.status]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const toggleCandidate = useCallback((id: string) => {
    setSelectedCandidates((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleJob = useCallback((id: string) => {
    setSelectedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAllCandidates = useCallback(() => {
    if (selectedCandidates.size === candidates.length) {
      setSelectedCandidates(new Set());
    } else {
      setSelectedCandidates(new Set(candidates.map((c) => c.id)));
    }
  }, [candidates, selectedCandidates.size]);

  const selectAllJobs = useCallback(() => {
    if (selectedJobs.size === jobs.length) {
      setSelectedJobs(new Set());
    } else {
      setSelectedJobs(new Set(jobs.map((j) => j.id)));
    }
  }, [jobs, selectedJobs.size]);

  const totalPairs = selectedCandidates.size * selectedJobs.size;

  // Launch batch
  const handleLaunchBatch = useCallback(async () => {
    if (totalPairs === 0) return;
    setLaunching(true);
    setError(null);

    try {
      const res = await analysisApi.triggerBatch(
        Array.from(selectedCandidates),
        Array.from(selectedJobs),
        forceReanalyze,
      );
      setBatchStatus(res.data);
      setSearchParams({ detail: res.data.batch_id });

      // Start polling
      const batchId = res.data.batch_id;
      pollRef.current = setInterval(async () => {
        try {
          const progress = await analysisApi.getBatchProgress(batchId);
          setBatchStatus(progress.data);

          if (progress.data.status !== "processing") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            // Refresh saved batches list
            analysisApi.listBatches().then((r) => setSavedBatches(r.data || []));
          }
        } catch (err) {
          console.error("Poll error:", err);
        }
      }, 2000);
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || "Failed to start batch analysis");
    } finally {
      setLaunching(false);
    }
  }, [totalPairs, selectedCandidates, selectedJobs, forceReanalyze]);

  const handleDeleteBatch = async () => {
    if (!deleteBatchId) return;
    setDeleteLoading(true);
    try {
      await analysisApi.deleteBatch(deleteBatchId);
      setSavedBatches((prev) => prev.filter((b) => b.batch_id !== deleteBatchId));
      if (batchStatus?.batch_id === deleteBatchId) {
        setBatchStatus(null);
        setSearchParams({});
      }
    } catch (err) {
      console.error(err);
    } finally {
      setDeleteLoading(false);
      setDeleteBatchId(null);
    }
  };

  const goBack = () => {
    setBatchStatus(null);
    setCompletionPhase("none");
    prevStatusRef.current = null;
    batchStartRef.current = null;
    setLiveElapsed(0);
    setSearchParams({});
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    // Refresh saved batches
    analysisApi.listBatches().then((r) => setSavedBatches(r.data || []));
  };

  const openBatchDetail = (batchId: string) => {
    setCompletionPhase("none");
    prevStatusRef.current = null;
    analysisApi.getBatchProgress(batchId).then((r) => {
      setBatchStatus(r.data);
      setSearchParams({ detail: batchId });
    });
  };

  const isRunning = batchStatus?.status === "processing";
  const isDone = batchStatus && batchStatus.status !== "processing";
  const isCelebrating = completionPhase === "celebrating";

  if (loading) {
    return (
      <div className="page-container-wide">
        {/* Skeleton loading */}
        <div className="h-4 w-40 shimmer rounded-lg mb-6" />
        <div className="mb-8">
          <div className="h-7 w-52 shimmer rounded-lg mb-2" />
          <div className="h-4 w-80 shimmer rounded-lg" />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {[1, 2].map((i) => (
            <div key={i} className="glass-card-solid p-6">
              <div className="h-5 w-28 shimmer rounded-lg mb-4" />
              <div className="space-y-2">
                {[1, 2, 3, 4].map((j) => (
                  <div key={j} className="h-10 shimmer rounded-lg" />
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="glass-card-solid p-6 shimmer h-20 mb-10" />
        <div className="h-6 w-36 shimmer rounded-lg mb-4" />
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="glass-card-solid p-5 h-36 shimmer" />
          ))}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  // Detail / Running view (viewing a specific batch)
  // ═══════════════════════════════════════════════════════════════════
  if (batchStatus) {
    return (
      <div className="page-container-wide animate-fade-in">
        {/* Back to batch home */}
        <button
          onClick={goBack}
          className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary mb-6 transition-colors"
        >
          <ArrowLeft size={15} />
          Back to Batch Analysis
        </button>

        <div className="space-y-6">
          {/* ── Celebration banner ──────────────────────────────────── */}
          {isCelebrating && (
            <div className="glass-card-solid p-5 bg-gradient-to-r from-emerald-50 to-blue-50 border border-emerald-200 animate-bounce-in">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-xl bg-emerald-100 flex items-center justify-center animate-float">
                  <PartyPopper size={24} className="text-emerald-600" />
                </div>
                <div className="flex-1">
                  <h3 className="text-base font-semibold text-emerald-800">
                    Batch Analysis Complete!
                  </h3>
                  <p className="text-sm text-emerald-600 mt-0.5">
                    {batchStatus.completed} pipeline{batchStatus.completed !== 1 ? "s" : ""} finished
                    {batchStatus.failed > 0 && ` · ${batchStatus.failed} failed`}
                    {batchStatus.elapsed_ms ? ` in ${(batchStatus.elapsed_ms / 1000).toFixed(1)}s` : ""}
                  </p>
                </div>
                {batchStatus.avg_score !== undefined && batchStatus.avg_score > 0 && (
                  <div className="text-right">
                    <p className="text-2xs text-emerald-500 uppercase tracking-wider font-medium">Avg Score</p>
                    <p className="text-2xl font-bold text-emerald-700 animate-scale-in">
                      {(batchStatus.avg_score * 100).toFixed(0)}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Progress header */}
          <div className={`glass-card-solid p-5 transition-all duration-700 ${
            isCelebrating ? "ring-2 ring-emerald-300 shadow-lg" : ""
          }`}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                {isRunning ? (
                  <div className="relative">
                    <Loader2 size={20} className="animate-spin text-brand-500" />
                    <div className="absolute inset-0 animate-ping opacity-20">
                      <Loader2 size={20} className="text-brand-500" />
                    </div>
                  </div>
                ) : batchStatus.status === "completed" ? (
                  <div className={isCelebrating ? "animate-bounce-in" : ""}>
                    <CheckCircle2 size={20} className="text-emerald-500" />
                  </div>
                ) : (
                  <AlertCircle size={20} className="text-amber-500" />
                )}
                <div>
                  <h2 className="text-base font-semibold text-text-primary">
                    {isRunning
                      ? "Processing pipelines..."
                      : batchStatus.status === "completed"
                      ? "Batch complete"
                      : "Batch finished with errors"}
                  </h2>
                  <p className="text-xs text-text-tertiary mt-0.5">
                    {batchStatus.completed} of {batchStatus.total} done
                    {batchStatus.cached > 0 && ` (${batchStatus.cached} cached)`}
                    {batchStatus.failed > 0 && ` · ${batchStatus.failed} failed`}
                    {liveElapsed > 0 ? ` · ${formatElapsed(liveElapsed)}` : ""}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isDone && batchStatus.created_at && (
                  <span className="text-xs text-text-tertiary">
                    {new Date(batchStatus.created_at).toLocaleDateString("en-US", {
                      month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
                    })}
                  </span>
                )}
              </div>
            </div>

            {/* Progress bar */}
            <div className="w-full bg-surface-tertiary rounded-full h-2.5 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-700 ease-out ${
                  isRunning ? "progress-stripe bg-brand-500" :
                  isCelebrating ? "bg-emerald-500" :
                  batchStatus.failed > 0 ? "bg-amber-500" : "bg-emerald-500"
                }`}
                style={{
                  width: `${batchStatus.total > 0 ? (batchStatus.completed / batchStatus.total) * 100 : 0}%`,
                }}
              />
            </div>

            {/* Individual pipeline progress during processing */}
            {isRunning && batchStatus.results && batchStatus.results.length > 0 && (
              <div className="mt-4 pt-4 border-t border-surface-border">
                <p className="text-2xs text-text-tertiary uppercase tracking-wider mb-2">Completed so far</p>
                <div className="flex flex-wrap gap-2">
                  {batchStatus.results
                    .filter((r) => !r.error)
                    .slice(-6)
                    .map((r, i) => (
                      <div
                        key={`${r.candidate_id}-${r.job_id}`}
                        className="flex items-center gap-2 px-3 py-1.5 bg-emerald-50 rounded-lg text-xs animate-scale-in"
                        style={{ animationDelay: `${i * 80}ms` }}
                      >
                        <CheckCircle2 size={11} className="text-emerald-500" />
                        <span className="text-emerald-700 font-medium">{r.candidate_name}</span>
                        <span className="text-emerald-600 font-bold">
                          {(r.overall_score * 100).toFixed(0)}
                        </span>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* Summary stats row — revealed after celebration */}
            {isDone && !isCelebrating && (
              <div className="flex items-center gap-8 mt-5 pt-5 border-t border-surface-border animate-fade-in">
                {batchStatus.job_titles && batchStatus.job_titles.length > 0 && (
                  <div>
                    <p className="text-2xs text-text-tertiary uppercase tracking-wider">Jobs</p>
                    <p className="text-sm font-medium text-text-primary mt-0.5">
                      {batchStatus.job_titles.join(", ")}
                    </p>
                  </div>
                )}
                {batchStatus.candidate_count !== undefined && batchStatus.candidate_count > 0 && (
                  <div>
                    <p className="text-2xs text-text-tertiary uppercase tracking-wider">Candidates</p>
                    <p className="text-sm font-semibold text-text-primary mt-0.5">{batchStatus.candidate_count}</p>
                  </div>
                )}
                {batchStatus.avg_score !== undefined && batchStatus.avg_score > 0 && (
                  <div>
                    <p className="text-2xs text-text-tertiary uppercase tracking-wider">Avg. Score</p>
                    <p className="text-sm font-semibold text-text-primary mt-0.5">
                      {(batchStatus.avg_score * 100).toFixed(0)}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Results — ranked leaderboard grouped by job */}
          {batchStatus.results && batchStatus.results.length > 0 && !isCelebrating && (
            <div className="space-y-6">
              {Array.from(new Set(batchStatus.results.map((r) => r.job_id))).map((jobId, groupIdx) => {
                const jobResults = batchStatus.results
                  .filter((r) => r.job_id === jobId && !r.error)
                  .sort((a, b) => b.overall_score - a.overall_score);
                const failedResults = batchStatus.results.filter(
                  (r) => r.job_id === jobId && r.error
                );
                const jobTitle = jobResults[0]?.job_title || failedResults[0]?.job_title || "Unknown Job";
                const maxScore = jobResults.length > 0 ? Math.max(...jobResults.map((r) => r.overall_score)) : 1;

                return (
                  <div
                    key={jobId}
                    className="glass-card-solid overflow-hidden stagger-item"
                    style={{ animationDelay: `${groupIdx * 120}ms` }}
                  >
                    {/* Job header */}
                    <div className="px-6 py-4 bg-surface-secondary/50 border-b border-surface-border flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <Briefcase size={16} className="text-brand-500" />
                        <div>
                          <h3 className="text-sm font-semibold text-text-primary">{jobTitle}</h3>
                          <p className="text-xs text-text-tertiary">
                            {jobResults.length} candidate{jobResults.length !== 1 ? "s" : ""} ranked
                          </p>
                        </div>
                      </div>
                      {isDone && (
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => handleExportBrief(batchStatus.batch_id, jobId)}
                            disabled={exporting && exportingJobId === jobId}
                            className="inline-flex items-center gap-1.5 text-xs text-text-tertiary hover:text-brand-600 font-medium px-2.5 py-1.5 rounded-md border border-surface-border hover:border-brand-200 hover:bg-brand-50 transition-colors disabled:opacity-50"
                          >
                            {exporting && exportingJobId === jobId ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <Download size={13} />
                            )}
                            Export Brief
                          </button>
                          <button
                            onClick={() => navigate(`/ranked/${jobId}?from=batch&batchId=${batchStatus.batch_id}`)}
                            className="inline-flex items-center gap-1 text-xs text-brand-500 hover:text-brand-600 font-medium group"
                          >
                            View full rankings
                            <ChevronRight size={14} className="group-hover:translate-x-0.5 transition-transform" />
                          </button>
                        </div>
                      )}
                    </div>

                    {/* Ranked list */}
                    <div className="divide-y divide-surface-border">
                      {jobResults.map((r, i) => {
                        const scorePercent = Math.round(r.overall_score * 100);
                        const scoreColor = scorePercent >= 75 ? "bg-emerald-500" : scorePercent >= 60 ? "bg-blue-500" : scorePercent >= 40 ? "bg-amber-500" : "bg-red-500";

                        return (
                          <div
                            key={`${r.candidate_id}-${r.job_id}`}
                            className="flex items-center gap-4 px-6 py-4 hover:bg-surface-hover transition-all cursor-pointer group stagger-item"
                            style={{ animationDelay: `${(groupIdx * 120) + (i * 50)}ms` }}
                            onClick={() => r.analysis_id && navigate(`/analysis/${r.analysis_id}?from=batch&batchId=${batchStatus.batch_id}`)}
                          >
                            {/* Rank */}
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-sm font-bold ${
                              i === 0 ? "bg-amber-100 text-amber-700" :
                              i === 1 ? "bg-gray-100 text-gray-600" :
                              i === 2 ? "bg-orange-100 text-orange-600" :
                              "bg-surface-tertiary text-text-tertiary"
                            }`}>
                              {i === 0 ? <Trophy size={14} /> : i + 1}
                            </div>

                            {/* Candidate info */}
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium text-text-primary group-hover:text-brand-500 transition-colors">
                                {r.candidate_name}
                              </p>
                            </div>

                            {/* Score bar + badge */}
                            <div className="flex items-center gap-3">
                              {r.cached && (
                                <span className="text-2xs text-text-tertiary bg-surface-tertiary px-2 py-0.5 rounded">
                                  cached
                                </span>
                              )}
                              {r.recommendation && (
                                <RecommendationBadge recommendation={r.recommendation} size="sm" />
                              )}
                              <div className="flex items-center gap-2 min-w-[100px]">
                                <span className={`text-lg font-bold min-w-[32px] text-right ${
                                  scorePercent >= 75 ? "text-emerald-600" :
                                  scorePercent >= 60 ? "text-blue-600" :
                                  scorePercent >= 40 ? "text-amber-600" :
                                  "text-red-500"
                                }`}>
                                  {scorePercent}
                                </span>
                                <div className="w-16 h-1.5 bg-surface-tertiary rounded-full overflow-hidden">
                                  <div
                                    className={`h-full rounded-full ${scoreColor} transition-all duration-700 ease-out`}
                                    style={{ width: `${(r.overall_score / maxScore) * 100}%` }}
                                  />
                                </div>
                              </div>
                            </div>
                          </div>
                        );
                      })}

                      {/* Failed items */}
                      {failedResults.map((r) => (
                        <div
                          key={`${r.candidate_id}-${r.job_id}-err`}
                          className="flex items-center gap-4 px-6 py-4 bg-red-50/50"
                        >
                          <XCircle size={16} className="text-red-400 flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary">{r.candidate_name}</p>
                            <p className="text-xs text-red-500">{r.error}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Empty results placeholder during celebration */}
          {isCelebrating && batchStatus.results && batchStatus.results.length > 0 && (
            <div className="glass-card-solid p-5 text-center animate-fade-in">
              <BarChart3 size={24} className="text-emerald-400 mx-auto mb-2 animate-float" />
              <p className="text-sm text-text-secondary">Loading ranked results...</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ═══════════════════════════════════════════════════════════════════
  // Home view — new batch form + past batches
  // ═══════════════════════════════════════════════════════════════════
  return (
    <div className="page-container-wide animate-fade-in">
      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-text-primary tracking-tight flex items-center gap-2">
          <Zap size={22} className="text-brand-500" />
          Batch Analysis
        </h1>
        <p className="text-sm text-text-secondary mt-1">
          Select candidates and jobs, then run all pipelines simultaneously. Results are ranked by score and saved for future reference.
        </p>
      </div>

      {/* ── Selection panels ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Candidates panel */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <Users size={15} className="text-brand-500" />
              Candidates
              <span className="text-xs text-text-tertiary font-normal">
                ({selectedCandidates.size} of {candidates.length})
              </span>
            </h2>
            <button
              onClick={selectAllCandidates}
              className="text-xs text-brand-500 hover:text-brand-600 font-medium"
            >
              {selectedCandidates.size === candidates.length ? "Deselect all" : "Select all"}
            </button>
          </div>

          {candidates.length === 0 ? (
            <div className="py-8 text-center">
              <Users size={20} className="text-text-tertiary mx-auto mb-2" />
              <p className="text-sm text-text-tertiary">No candidates uploaded yet</p>
              <p className="text-xs text-brand-500 font-medium mt-1">Upload resumes on the Candidates page</p>
            </div>
          ) : (
            <div className="max-h-[400px] overflow-y-auto space-y-1 -mx-2 px-2">
              {candidates.map((c) => (
                <label
                  key={c.id}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
                    selectedCandidates.has(c.id)
                      ? "bg-brand-50 ring-1 ring-brand-200"
                      : "hover:bg-surface-hover"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedCandidates.has(c.id)}
                    onChange={() => toggleCandidate(c.id)}
                    className="w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-brand-500"
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-text-primary truncate">{c.name}</p>
                    {c.current_role && (
                      <p className="text-xs text-text-tertiary truncate">{c.current_role}</p>
                    )}
                  </div>
                  {c.years_experience !== undefined && c.years_experience !== null && (
                    <span className="text-2xs text-text-tertiary bg-surface-tertiary px-2 py-0.5 rounded">
                      {c.years_experience < 1 ? "<1" : Math.round(c.years_experience)}y
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
        </div>

        {/* Jobs panel */}
        <div className="glass-card-solid p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <Briefcase size={15} className="text-brand-500" />
              Jobs
              <span className="text-xs text-text-tertiary font-normal">
                ({selectedJobs.size} of {jobs.length})
              </span>
            </h2>
            <button
              onClick={selectAllJobs}
              className="text-xs text-brand-500 hover:text-brand-600 font-medium"
            >
              {selectedJobs.size === jobs.length ? "Deselect all" : "Select all"}
            </button>
          </div>

          {jobs.length === 0 ? (
            <div className="py-8 text-center">
              <Briefcase size={20} className="text-text-tertiary mx-auto mb-2" />
              <p className="text-sm text-text-tertiary">No jobs created yet</p>
              <p className="text-xs text-brand-500 font-medium mt-1">Create jobs on the Jobs page</p>
            </div>
          ) : (
            <div className="max-h-[400px] overflow-y-auto space-y-1 -mx-2 px-2">
              {jobs.map((j) => (
                <label
                  key={j.id}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors ${
                    selectedJobs.has(j.id)
                      ? "bg-brand-50 ring-1 ring-brand-200"
                      : "hover:bg-surface-hover"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedJobs.has(j.id)}
                    onChange={() => toggleJob(j.id)}
                    className="w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-brand-500"
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-text-primary truncate">{j.title}</p>
                    {j.company && (
                      <p className="text-xs text-text-tertiary truncate">{j.company}</p>
                    )}
                  </div>
                  {j.required_skills && (
                    <span className="text-2xs text-text-tertiary bg-surface-tertiary px-2 py-0.5 rounded">
                      {j.required_skills.length} skills
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Launch bar ──────────────────────────────────────────── */}
      <div className="glass-card-solid p-5 mb-7">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div>
              <p className="text-sm font-semibold text-text-primary">
                {totalPairs > 0
                  ? `${selectedCandidates.size} candidate${selectedCandidates.size !== 1 ? "s" : ""} × ${selectedJobs.size} job${selectedJobs.size !== 1 ? "s" : ""} = ${totalPairs} pipeline run${totalPairs !== 1 ? "s" : ""}`
                  : "Select candidates and jobs to begin"}
              </p>
              {totalPairs > 0 && (
                <p className="text-xs text-text-tertiary mt-0.5">
                  Estimated time: ~{Math.ceil(totalPairs / 8) * 20}s
                  {!forceReanalyze && " (existing analyses will be reused)"}
                </p>
              )}
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={forceReanalyze}
                onChange={(e) => setForceReanalyze(e.target.checked)}
                className="w-3.5 h-3.5 rounded border-gray-300 text-brand-500 focus:ring-brand-500"
              />
              <span className="text-xs text-text-secondary">Force re-analyze</span>
            </label>
          </div>
          <button
            onClick={handleLaunchBatch}
            disabled={totalPairs === 0 || launching}
            className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {launching ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Starting...
              </>
            ) : (
              <>
                <Zap size={16} />
                Run Batch Analysis
              </>
            )}
          </button>
        </div>

        {error && (
          <div className="mt-4 p-3 bg-red-50 rounded-lg border border-red-100 flex items-center gap-2">
            <AlertCircle size={14} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════ */}
      {/* ── Past Batches (Analysis Log) ─────────────────────────── */}
      {/* ═══════════════════════════════════════════════════════════ */}
      <div>
        <h2 className="text-base font-semibold text-text-primary mb-4 flex items-center gap-2">
          <Clock size={18} className="text-text-tertiary" />
          Analysis History
        </h2>

        {loadingSaved ? (
          <div className="glass-card-solid p-5 flex items-center justify-center">
            <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : savedBatches.length === 0 ? (
          <div className="glass-card-solid p-10 text-center">
            <Zap size={28} className="text-text-tertiary mx-auto mb-3" />
            <p className="text-base font-medium text-text-primary">No past analyses</p>
            <p className="text-sm text-text-tertiary mt-1">
              Run a batch analysis above to start building your history.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {savedBatches.map((batch, batchIdx) => {
              const isProcessing = batch.status === "processing";

              return (
                <div
                  key={batch.batch_id}
                  className="glass-card-solid p-4 hover:ring-1 hover:ring-brand-200 transition-all cursor-pointer group relative hover-lift stagger-item"
                  style={{ animationDelay: `${batchIdx * 60}ms` }}
                  onClick={() => openBatchDetail(batch.batch_id)}
                >
                  {/* Delete button is in the footer row below */}

                  {/* Status indicator + jobs */}
                  <div className="flex items-start gap-3 mb-3">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
                      isProcessing ? "bg-brand-50" :
                      batch.status === "completed" ? "bg-emerald-50" :
                      "bg-amber-50"
                    }`}>
                      {isProcessing ? (
                        <Loader2 size={14} className="animate-spin text-brand-500" />
                      ) : batch.status === "completed" ? (
                        <CheckCircle2 size={14} className="text-emerald-500" />
                      ) : (
                        <AlertCircle size={14} className="text-amber-500" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-text-primary truncate">
                        {batch.job_titles && batch.job_titles.length > 0
                          ? batch.job_titles.join(", ")
                          : `Batch ${batch.batch_id}`}
                      </p>
                      <p className="text-xs text-text-tertiary mt-0.5">
                        {batch.candidate_count || 0} candidate{(batch.candidate_count || 0) !== 1 ? "s" : ""}
                        {" · "}
                        {batch.total} pipeline{batch.total !== 1 ? "s" : ""}
                      </p>
                    </div>
                  </div>

                  {/* Stats row */}
                  <div className="flex items-center gap-4 text-xs">
                    {batch.avg_score !== undefined && batch.avg_score > 0 && (
                      <div className="flex items-center gap-1">
                        <span className="text-text-tertiary">Avg:</span>
                        <span className={`font-semibold ${
                          batch.avg_score >= 0.75 ? "text-emerald-600" :
                          batch.avg_score >= 0.60 ? "text-blue-600" :
                          batch.avg_score >= 0.40 ? "text-amber-600" :
                          "text-red-500"
                        }`}>
                          {(batch.avg_score * 100).toFixed(0)}
                        </span>
                      </div>
                    )}
                    {batch.top_recommendation && (
                      <RecommendationBadge recommendation={batch.top_recommendation} size="sm" />
                    )}
                    {batch.elapsed_ms && (
                      <span className="text-text-tertiary">
                        {(batch.elapsed_ms / 1000).toFixed(1)}s
                      </span>
                    )}
                  </div>

                  {/* Footer: date + delete */}
                  <div className="flex items-center justify-between mt-3 pt-3 border-t border-surface-border">
                    {batch.created_at ? (
                      <p className="text-2xs text-text-tertiary">
                        {new Date(batch.created_at).toLocaleDateString("en-US", {
                          month: "short", day: "numeric", year: "numeric",
                          hour: "numeric", minute: "2-digit"
                        })}
                      </p>
                    ) : <div />}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteBatchId(batch.batch_id);
                      }}
                      className="inline-flex items-center gap-1 text-2xs text-text-tertiary hover:text-red-500 hover:bg-red-50 px-2 py-1 rounded-md transition-colors"
                    >
                      <Trash2 size={12} />
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteBatchId}
        title="Delete batch history?"
        description="This will remove this batch from history. The individual analysis results will remain available."
        actionText="Delete"
        danger
        loading={deleteLoading}
        onConfirm={handleDeleteBatch}
        onCancel={() => setDeleteBatchId(null)}
      />
    </div>
  );
}
