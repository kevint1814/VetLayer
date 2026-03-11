import { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Users, Briefcase, Upload, ArrowRight, Clock, FileText,
  Zap, CheckCircle2, Loader2, AlertCircle, FilePlus, Sparkles, X
} from "lucide-react";
import { healthApi, candidatesApi, jobsApi } from "@/services/api";
import type { Candidate } from "@/types";

function AnimatedCount({ target, duration = 800 }: { target: number; duration?: number }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (target === 0) return;
    let start = 0;
    const increment = target / (duration / 16);
    const timer = setInterval(() => {
      start += increment;
      if (start >= target) {
        setCount(target);
        clearInterval(timer);
      } else {
        setCount(Math.floor(start));
      }
    }, 16);
    return () => clearInterval(timer);
  }, [target, duration]);
  return <>{count}</>;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<string>("checking");
  const [candidateCount, setCandidateCount] = useState(0);
  const [jobCount, setJobCount] = useState(0);
  const [recentCandidates, setRecentCandidates] = useState<Candidate[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<{ name: string; status: "pending" | "uploading" | "done" | "error" }[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploadSuccess, setUploadSuccess] = useState(false);

  useEffect(() => {
    Promise.all([
      healthApi.check().then(() => setStatus("online")).catch(() => setStatus("offline")),
      candidatesApi.list({ limit: 5 }).then((r) => {
        setCandidateCount(r.data.total);
        setRecentCandidates(r.data.candidates);
      }).catch(() => {}),
      jobsApi.list({ limit: 1 }).then((r) => setJobCount(r.data.total)).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  const handleUpload = useCallback(async (files: File[]) => {
    const validFiles = files.filter((f) => f.name.match(/\.(pdf|docx|txt)$/i));
    if (validFiles.length === 0) {
      setError("No valid files. Supported: PDF, DOCX, TXT");
      return;
    }

    if (validFiles.length === 1 && validFiles[0]) {
      setUploading(true);
      setUploadFiles([{ name: validFiles[0].name, status: "uploading" }]);
      try {
        const res = await candidatesApi.upload(validFiles[0]);
        setUploadFiles([{ name: validFiles[0].name, status: "done" }]);
        setTimeout(() => navigate(`/candidates/${res.data.id}`), 400);
      } catch {
        setUploadFiles([{ name: validFiles[0].name, status: "error" }]);
        setError("Failed to upload resume. Please try again.");
        setUploading(false);
      }
      return;
    }

    setUploading(true);
    setUploadFiles(validFiles.map((f) => ({ name: f.name, status: "pending" as const })));
    setError(null);

    try {
      setUploadFiles(validFiles.map((f) => ({ name: f.name, status: "uploading" as const })));
      const res = await candidatesApi.bulkUpload(validFiles);
      const failedNames = new Set((res.data.failed || []).map((f: any) => f.filename));
      setUploadFiles(validFiles.map((f) => ({
        name: f.name,
        status: failedNames.has(f.name) ? "error" as const : "done" as const,
      })));
      setUploadSuccess(true);
      setTimeout(() => { setUploadSuccess(false); setUploadFiles([]); setUploading(false); }, 3000);
      candidatesApi.list({ limit: 5 }).then((r) => {
        setCandidateCount(r.data.total);
        setRecentCandidates(r.data.candidates);
      });
      if (res.data.total_failed > 0) {
        setError(`${res.data.total_created} uploaded, ${res.data.total_failed} failed.`);
      }
    } catch {
      setUploadFiles(validFiles.map((f) => ({ name: f.name, status: "error" as const })));
      setError("Bulk upload failed. Please try again.");
      setUploading(false);
    }
  }, [navigate]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) handleUpload(files);
  }, [handleUpload]);

  const onFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) handleUpload(files);
    e.target.value = "";
  }, [handleUpload]);

  if (loading) {
    return (
      <div className="page-container">
        <div className="mb-8">
          <div className="h-6 w-48 shimmer rounded-lg mb-2" />
          <div className="h-4 w-72 shimmer rounded-lg" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-7">
          {[1,2,3].map((i) => (
            <div key={i} className="glass-card-solid p-5">
              <div className="h-4 w-20 shimmer rounded mb-3" />
              <div className="h-8 w-14 shimmer rounded-lg mb-1.5" />
              <div className="h-3 w-24 shimmer rounded" />
            </div>
          ))}
        </div>
        <div className="glass-card-solid shimmer h-36 mb-7" />
      </div>
    );
  }

  return (
    <div className="page-container animate-fade-in">
      {/* ── Error ──────────────────────────────────────────────── */}
      {error && (
        <div className="mb-5 p-3.5 bg-red-50 rounded-lg border border-red-100 flex items-center justify-between animate-slide-up">
          <div className="flex items-center gap-2">
            <AlertCircle size={14} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 ml-4 p-0.5">
            <X size={14} />
          </button>
        </div>
      )}

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="mb-7">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-primary tracking-tight">
              Welcome back
            </h1>
            <p className="text-sm text-text-tertiary mt-0.5">
              Here's an overview of your recruiting pipeline.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-md ${
              status === "online"
                ? "text-emerald-600 bg-emerald-50"
                : status === "offline"
                ? "text-red-600 bg-red-50"
                : "text-text-tertiary bg-surface-tertiary"
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full ${status === "online" ? "bg-emerald-500" : status === "offline" ? "bg-red-500" : "bg-text-tertiary"}`} />
              {status === "checking" ? "Connecting" : status === "online" ? "Online" : "Offline"}
            </span>
          </div>
        </div>
      </div>

      {/* ── Stats row ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-7">
        {/* Candidates */}
        <div className="glass-card-solid p-5 hover-lift">
          <p className="text-xs text-text-tertiary font-medium uppercase tracking-wider mb-1">Candidates</p>
          <div className="flex items-end justify-between">
            <p className="text-2xl font-semibold text-text-primary leading-none">
              <AnimatedCount target={candidateCount} />
            </p>
            <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
              <Users size={15} className="text-blue-600" />
            </div>
          </div>
        </div>

        {/* Jobs */}
        <div className="glass-card-solid p-5 hover-lift">
          <p className="text-xs text-text-tertiary font-medium uppercase tracking-wider mb-1">Active Jobs</p>
          <div className="flex items-end justify-between">
            <p className="text-2xl font-semibold text-text-primary leading-none">
              <AnimatedCount target={jobCount} />
            </p>
            <div className="w-8 h-8 rounded-lg bg-violet-50 flex items-center justify-center">
              <Briefcase size={15} className="text-violet-600" />
            </div>
          </div>
        </div>

        {/* Batch Analysis */}
        <Link to="/batch" className="glass-card-solid p-5 hover-lift group">
          <p className="text-xs text-text-tertiary font-medium uppercase tracking-wider mb-1">Batch Analysis</p>
          <div className="flex items-end justify-between">
            <p className="text-sm font-medium text-text-secondary leading-none">
              Run multi-candidate pipelines
            </p>
            <div className="w-8 h-8 rounded-lg bg-emerald-50 flex items-center justify-center group-hover:scale-105 transition-transform">
              <Zap size={15} className="text-emerald-600" />
            </div>
          </div>
        </Link>
      </div>

      {/* ── Upload zone ──────────────────────────────────────────── */}
      <div
        className={`glass-card-solid p-10 text-center mb-7 transition-all duration-300 cursor-pointer relative overflow-hidden
          ${dragOver ? "drop-zone-active border-2 border-dashed border-brand-400" : "border border-dashed border-surface-border hover:border-brand-300 hover:shadow"}
          ${uploading ? "pointer-events-none" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => !uploading && document.getElementById("file-upload")?.click()}
      >
        <input
          id="file-upload"
          type="file"
          accept=".pdf,.docx,.txt"
          multiple
          className="hidden"
          onChange={onFileSelect}
        />

        {uploadFiles.length > 0 ? (
          <div className="space-y-3">
            <div className="w-12 h-12 rounded-xl bg-brand-50 flex items-center justify-center mx-auto mb-3">
              {uploadSuccess ? (
                <CheckCircle2 size={22} className="text-emerald-500 animate-bounce-in" />
              ) : (
                <Loader2 size={22} className="text-brand-500 animate-spin" />
              )}
            </div>
            <p className="text-sm font-medium text-text-primary">
              {uploadSuccess ? "Upload complete!" : `Processing ${uploadFiles.length} file${uploadFiles.length > 1 ? "s" : ""}...`}
            </p>
            <div className="max-w-xs mx-auto space-y-1.5 mt-3">
              {uploadFiles.map((f, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2.5 px-3 py-1.5 bg-surface-secondary rounded-lg text-left animate-fade-in-up"
                  style={{ animationDelay: `${i * 80}ms` }}
                >
                  <FileText size={13} className="text-text-tertiary flex-shrink-0" />
                  <span className="text-xs text-text-secondary flex-1 truncate">{f.name}</span>
                  {f.status === "uploading" && <Loader2 size={11} className="animate-spin text-brand-500" />}
                  {f.status === "done" && <CheckCircle2 size={11} className="text-emerald-500" />}
                  {f.status === "error" && <AlertCircle size={11} className="text-red-500" />}
                  {f.status === "pending" && <Clock size={11} className="text-text-tertiary" />}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className={`w-12 h-12 rounded-xl bg-surface-tertiary flex items-center justify-center mx-auto mb-3 transition-transform duration-200 ${dragOver ? "scale-110 animate-float" : ""}`}>
              <Upload size={20} className="text-text-tertiary" />
            </div>
            <p className="text-sm font-medium text-text-primary">
              Drop resumes here to get started
            </p>
            <p className="text-xs text-text-tertiary mt-1">
              Drag and drop or click to browse — PDF, DOCX, TXT
            </p>
            <div className="flex items-center justify-center gap-1.5 mt-3">
              <FilePlus size={12} className="text-brand-400" />
              <span className="text-2xs font-medium text-brand-500">
                Multiple files supported
              </span>
            </div>
          </>
        )}
      </div>

      {/* ── Quick navigation ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-7">
        <Link to="/candidates" className="glass-card-solid p-4 group hover-lift">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center">
                <Users size={16} className="text-blue-600" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-text-primary">View All Candidates</h3>
                <p className="text-xs text-text-tertiary mt-0.5">Browse and manage profiles</p>
              </div>
            </div>
            <ArrowRight size={16} className="text-text-tertiary group-hover:text-brand-500 group-hover:translate-x-0.5 transition-all" />
          </div>
        </Link>

        <Link to="/jobs" className="glass-card-solid p-4 group hover-lift">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-violet-50 flex items-center justify-center">
                <Briefcase size={16} className="text-violet-600" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-text-primary">Manage Jobs</h3>
                <p className="text-xs text-text-tertiary mt-0.5">Create and edit job requirements</p>
              </div>
            </div>
            <ArrowRight size={16} className="text-text-tertiary group-hover:text-brand-500 group-hover:translate-x-0.5 transition-all" />
          </div>
        </Link>
      </div>

      {/* ── Recent candidates ────────────────────────────────────── */}
      {recentCandidates.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <p className="section-heading">Recent Candidates</p>
            <Link to="/candidates" className="text-xs font-medium text-brand-500 hover:text-brand-600 transition-colors">
              View all
            </Link>
          </div>
          <div className="glass-card-solid divide-y divide-surface-border overflow-hidden">
            {recentCandidates.map((c, i) => (
              <Link
                key={c.id}
                to={`/candidates/${c.id}`}
                className="flex items-center gap-3 px-5 py-3.5 hover:bg-surface-hover transition-colors stagger-item"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="w-8 h-8 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0">
                  <span className="text-xs font-semibold text-brand-500">
                    {c.name.charAt(0).toUpperCase()}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-text-primary truncate">{c.name}</p>
                  <p className="text-xs text-text-tertiary truncate">
                    {c.current_role || "Role pending"}{c.current_company ? ` at ${c.current_company}` : ""}
                  </p>
                </div>
                <span className="text-2xs text-text-tertiary flex-shrink-0">
                  {new Date(c.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* ── Getting started hint ──────────────────────────────── */}
      {candidateCount === 0 && jobCount === 0 && (
        <div className="glass-card-solid p-8 text-center mt-7 animate-fade-in-up">
          <div className="w-12 h-12 rounded-xl bg-brand-50/60 flex items-center justify-center mx-auto mb-4">
            <Sparkles size={22} className="text-brand-400" />
          </div>
          <h3 className="text-sm font-semibold text-text-primary mb-1.5">Get started in 3 steps</h3>
          <div className="flex items-center justify-center gap-8 mt-5 text-left max-w-md mx-auto">
            {[
              { n: 1, title: "Upload resumes", sub: "PDF, DOCX, or TXT" },
              { n: 2, title: "Create jobs", sub: "Paste requirements" },
              { n: 3, title: "Run analysis", sub: "Smart vetting pipeline" },
            ].map(({ n, title, sub }) => (
              <div key={n} className="flex items-start gap-2.5">
                <div className="w-6 h-6 rounded-full bg-brand-500 text-white flex items-center justify-center text-2xs font-bold flex-shrink-0">{n}</div>
                <div>
                  <p className="text-sm font-medium text-text-primary">{title}</p>
                  <p className="text-xs text-text-tertiary mt-0.5">{sub}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
