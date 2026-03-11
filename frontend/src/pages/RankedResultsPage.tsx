import { useEffect, useState } from "react";
import { useParams, useNavigate, useSearchParams, Link } from "react-router-dom";
import {
  ArrowLeft, Trophy, Briefcase, AlertTriangle, Shield, BarChart3, Download
} from "lucide-react";
import { analysisApi } from "@/services/api";
import type { RankedResults } from "@/types";
import RecommendationBadge from "@/components/common/RecommendationBadge";

export default function RankedResultsPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<RankedResults | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fromBatch = searchParams.get("from") === "batch";
  const batchId = searchParams.get("batchId");
  const backUrl = fromBatch && batchId ? `/batch?detail=${batchId}` : "/batch";
  const backLabel = fromBatch ? "Back to Batch Results" : "Back to Batch Analysis";
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleExport = async () => {
    if (!batchId || !jobId) return;
    setExporting(true);
    setExportError(null);
    try {
      const res = await analysisApi.exportBatchBrief(batchId, jobId);
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `batch_brief_${batchId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed:", err);
      setExportError("Export failed. Please try again.");
      setTimeout(() => setExportError(null), 4000);
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    if (!jobId) return;
    analysisApi
      .getRanked(jobId)
      .then((r) => setData(r.data))
      .catch((err) => {
        console.error(err);
        setError("Failed to load rankings. Please try again.");
      })
      .finally(() => setLoading(false));
  }, [jobId]);

  if (loading) {
    return (
      <div className="page-container">
        <div className="h-4 w-32 shimmer rounded-lg mb-6" />
        <div className="glass-card-solid p-8 mb-6">
          <div className="flex items-start justify-between">
            <div className="space-y-3">
              <div className="h-6 w-48 shimmer rounded-lg" />
              <div className="h-4 w-36 shimmer rounded-lg" />
            </div>
            <div className="h-6 w-20 shimmer rounded-lg" />
          </div>
        </div>
        <div className="glass-card-solid overflow-hidden">
          {[1,2,3,4].map(i => (
            <div key={i} className="flex items-center gap-4 px-6 py-4 border-b border-surface-border">
              <div className="w-8 h-8 shimmer rounded-full" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-32 shimmer rounded" />
                <div className="h-3 w-48 shimmer rounded" />
              </div>
              <div className="h-6 w-12 shimmer rounded" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-10">
        <Link to="/batch" className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary mb-6 transition-colors">
          <ArrowLeft size={15} />
          Back to Batch Analysis
        </Link>
        <p className="text-text-secondary">{error || "No data found."}</p>
      </div>
    );
  }

  const scores = data.candidates.map((c) => c.overall_score);
  const avgScore = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 0;
  const maxScore = scores.length > 0 ? Math.max(...scores) : 1;
  const strongYes = data.candidates.filter((c) => c.recommendation === "strong_yes").length;
  const yes = data.candidates.filter((c) => c.recommendation === "yes").length;
  const maybe = data.candidates.filter((c) => c.recommendation === "maybe").length;
  const no = data.candidates.filter((c) => ["no", "strong_no"].includes(c.recommendation)).length;

  return (
    <div className="page-container animate-fade-in">
      {/* ── Back ─────────────────────────────────────────────────── */}
      <Link to={backUrl} className="inline-flex items-center gap-1.5 text-sm text-text-tertiary hover:text-text-primary mb-6 transition-colors">
        <ArrowLeft size={15} />
        {backLabel}
      </Link>

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="glass-card-solid p-6 mb-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-2">
              <Trophy size={20} className="text-amber-500" />
              <h1 className="text-xl font-semibold text-text-primary">Candidate Rankings</h1>
            </div>
            <div className="flex items-center gap-3 mt-1">
              <Briefcase size={14} className="text-text-tertiary" />
              <span className="text-sm text-text-secondary">
                {data.job_title}
                {data.job_company && <span className="text-text-tertiary"> at {data.job_company}</span>}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {batchId && (
              <button
                onClick={handleExport}
                disabled={exporting}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-text-secondary hover:text-brand-600 bg-surface-tertiary hover:bg-brand-50 px-3 py-1.5 rounded-lg transition-all disabled:opacity-50"
              >
                <Download size={14} />
                {exporting ? "Exporting…" : "Export Brief"}
              </button>
            )}
            <span className="text-sm text-text-tertiary bg-surface-tertiary px-3 py-1.5 rounded-lg">
              {data.total_candidates} candidate{data.total_candidates !== 1 ? "s" : ""}
            </span>
          </div>
        </div>

        {/* Export error */}
        {exportError && (
          <div className="mt-3 px-4 py-2 bg-red-50 text-red-600 text-sm rounded-lg border border-red-200">
            {exportError}
          </div>
        )}

        {/* Stats bar */}
        {data.candidates.length > 0 && (
          <div className="flex items-center gap-8 mt-6 pt-5 border-t border-surface-border">
            <div>
              <p className="text-2xs text-text-tertiary uppercase tracking-wider">Avg. Score</p>
              <p className="text-lg font-bold text-text-primary mt-0.5">{(avgScore * 100).toFixed(0)}</p>
            </div>
            {[
              { label: "Strong Yes", value: strongYes, color: "text-emerald-600" },
              { label: "Yes", value: yes, color: "text-blue-600" },
              { label: "Maybe", value: maybe, color: "text-amber-600" },
              { label: "No", value: no, color: "text-red-500" },
            ].map(({ label, value, color }) => (
              <div key={label}>
                <p className="text-2xs text-text-tertiary uppercase tracking-wider">{label}</p>
                <p className={`text-lg font-bold mt-0.5 ${color}`}>{value}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Ranked list ──────────────────────────────────────────── */}
      {data.candidates.length === 0 ? (
        <div className="glass-card-solid p-10 text-center">
          <BarChart3 size={28} className="text-text-tertiary mx-auto mb-3" />
          <p className="text-base font-medium text-text-primary">No analyses yet</p>
          <p className="text-sm text-text-tertiary mt-1">
            Run a batch analysis to see candidates ranked for this job.
          </p>
        </div>
      ) : (
        <div className="glass-card-solid overflow-hidden">
          {/* Table header */}
          <div className="grid grid-cols-[60px_1fr_120px_140px_80px_80px_60px] gap-4 px-6 py-3 bg-surface-secondary/50 border-b border-surface-border text-2xs text-text-tertiary uppercase tracking-wider font-medium">
            <div>Rank</div>
            <div>Candidate</div>
            <div>Recommendation</div>
            <div>Score</div>
            <div className="text-right">Skills</div>
            <div className="text-right">Depth</div>
            <div className="text-right">Risks</div>
          </div>

          {/* Rows */}
          {data.candidates.map((c, i) => {
            const scorePercent = Math.round(c.overall_score * 100);
            const scoreColor = scorePercent >= 75 ? "bg-emerald-500" : scorePercent >= 60 ? "bg-blue-500" : scorePercent >= 40 ? "bg-amber-500" : "bg-red-500";
            const scoreTextColor = scorePercent >= 75 ? "text-emerald-600" : scorePercent >= 60 ? "text-blue-600" : scorePercent >= 40 ? "text-amber-600" : "text-red-500";

            return (
              <div
                key={c.analysis_id}
                onClick={() => navigate(`/analysis/${c.analysis_id}?from=ranked&jobId=${jobId}${batchId ? `&batchId=${batchId}` : ""}`)}
                className="grid grid-cols-[60px_1fr_120px_140px_80px_80px_60px] gap-4 px-6 py-4 border-b border-surface-border hover:bg-surface-hover transition-all cursor-pointer group stagger-item"
                style={{ animationDelay: `${i * 50}ms` }}
              >
                {/* Rank */}
                <div className="flex items-center">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                    c.rank === 1 ? "bg-amber-100 text-amber-700" :
                    c.rank === 2 ? "bg-gray-100 text-gray-600" :
                    c.rank === 3 ? "bg-orange-100 text-orange-600" :
                    "bg-surface-tertiary text-text-tertiary"
                  }`}>
                    {c.rank === 1 ? <Trophy size={14} /> : c.rank}
                  </div>
                </div>

                {/* Candidate */}
                <div className="flex flex-col justify-center min-w-0">
                  <p className="text-sm font-medium text-text-primary truncate group-hover:text-brand-500 transition-colors">
                    {c.candidate_name}
                  </p>
                  {(c.current_role || c.current_company) && (
                    <p className="text-xs text-text-tertiary truncate mt-0.5">
                      {c.current_role}
                      {c.current_role && c.current_company && " at "}
                      {c.current_company}
                    </p>
                  )}
                </div>

                {/* Recommendation */}
                <div className="flex items-center">
                  <RecommendationBadge recommendation={c.recommendation} size="sm" />
                </div>

                {/* Score with bar */}
                <div className="flex items-center gap-3">
                  <span className={`text-lg font-bold min-w-[32px] ${scoreTextColor}`}>
                    {scorePercent}
                  </span>
                  <div className="flex-1 h-2 bg-surface-tertiary rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${scoreColor} transition-all duration-700 ease-out`}
                      style={{ width: `${(c.overall_score / maxScore) * 100}%` }}
                    />
                  </div>
                </div>

                {/* Skill match */}
                <div className="flex items-center justify-end">
                  <span className="text-sm text-text-secondary">
                    {(c.skill_match_score * 100).toFixed(0)}%
                  </span>
                </div>

                {/* Depth */}
                <div className="flex items-center justify-end">
                  <span className="text-sm text-text-secondary">
                    {(c.depth_score * 100).toFixed(0)}%
                  </span>
                </div>

                {/* Risk flags */}
                <div className="flex items-center justify-end">
                  {c.risk_flag_count > 0 ? (
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600 bg-amber-50 px-2 py-0.5 rounded">
                      <AlertTriangle size={10} />
                      {c.risk_flag_count}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded">
                      <Shield size={10} />
                      0
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
