import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Search, Users, MapPin, Building2, Calendar, Upload, Trash2,
  CheckCircle2, Loader2, AlertCircle, FileText, FilePlus, XCircle, Clock, Sparkles
} from "lucide-react";
import { candidatesApi } from "@/services/api";
import type { Candidate } from "@/types";
import { useMultiSelect } from "@/hooks/useMultiSelect";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import BulkActionBar from "@/components/common/BulkActionBar";

interface UploadFileState {
  name: string;
  size: string;
  status: "pending" | "uploading" | "done" | "error";
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / 1048576).toFixed(1)}MB`;
}

export default function CandidatesPage() {
  const navigate = useNavigate();
  const [allCandidates, setAllCandidates] = useState<Candidate[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Split candidates: only show ready/legacy ones in list
  const readyCandidates = useMemo(
    () => allCandidates.filter((c) => !c.processing_status || c.processing_status === "ready" || c.processing_status === "failed"),
    [allCandidates]
  );
  const processingCount = useMemo(
    () => allCandidates.filter((c) => c.processing_status && c.processing_status !== "ready" && c.processing_status !== "failed").length,
    [allCandidates]
  );

  // Track candidates without intelligence profile (for subtle bottom-right toast)
  const profilesPendingCount = useMemo(
    () => readyCandidates.filter((c) => c.processing_status === "ready" && !c.intelligence_profile).length,
    [readyCandidates]
  );

  // Multi-select operates on ready candidates only
  const { selectedIds, toggleItem, toggleAll, clear, isSelected, isAllSelected, isSomeSelected, count } =
    useMultiSelect(readyCandidates);

  // Confirm dialog state
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);

  // Upload state — card stays visible while files are processing
  const [uploadFiles, setUploadFiles] = useState<UploadFileState[]>([]);
  const [uploadApiDone, setUploadApiDone] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Computed: show upload card while files exist AND (api not done yet OR candidates still processing)
  const showUploadCard = uploadFiles.length > 0 && (!uploadApiDone || processingCount > 0);

  // Auto-dismiss upload card once all processing finishes
  const prevProcessingRef = useRef(processingCount);
  useEffect(() => {
    if (prevProcessingRef.current > 0 && processingCount === 0 && uploadApiDone) {
      // All done — dismiss after short delay
      const t = setTimeout(() => {
        setUploadFiles([]);
        setUploadApiDone(false);
      }, 1500);
      return () => clearTimeout(t);
    }
    prevProcessingRef.current = processingCount;
  }, [processingCount, uploadApiDone]);

  // Fetch candidates
  useEffect(() => {
    let cancelled = false;
    const delay = search ? 300 : 0;

    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await candidatesApi.list({ search: search || undefined, limit: 100 });
        if (!cancelled) {
          const seen = new Set<string>();
          const unique = res.data.candidates.filter((c: Candidate) => {
            if (seen.has(c.id)) return false;
            seen.add(c.id);
            return true;
          });
          setAllCandidates(unique);
        }
      } catch (err) {
        console.error("Failed to fetch candidates:", err);
        if (!cancelled) setError("Failed to load candidates. Please refresh the page.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, delay);

    return () => { cancelled = true; clearTimeout(timer); };
  }, [search]);

  // Auto-refresh while candidates are processing OR intelligence profiles pending
  useEffect(() => {
    if (processingCount === 0 && profilesPendingCount === 0) return;

    const interval = setInterval(async () => {
      try {
        const res = await candidatesApi.list({ search: search || undefined, limit: 100 });
        const seen = new Set<string>();
        const unique = res.data.candidates.filter((c: Candidate) => {
          if (seen.has(c.id)) return false;
          seen.add(c.id);
          return true;
        });
        setAllCandidates(unique);
      } catch {
        // silent
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [processingCount, profilesPendingCount, search]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "a" && !loading && readyCandidates.length > 0) {
        if ((e.target as HTMLElement).tagName === "INPUT") return;
        e.preventDefault();
        toggleAll();
      }
      if (e.key === "Delete" && isSomeSelected) {
        e.preventDefault();
        setConfirmOpen(true);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [loading, readyCandidates.length, isSomeSelected, toggleAll]);

  // Single file upload
  const handleUpload = useCallback(async (file: File) => {
    if (!file.name.match(/\.(pdf|docx|txt)$/i)) return;
    setUploadApiDone(false);
    setUploadFiles([{ name: file.name, size: formatFileSize(file.size), status: "uploading" }]);
    try {
      await candidatesApi.upload(file);
      setUploadFiles([{ name: file.name, size: formatFileSize(file.size), status: "done" }]);
      setUploadApiDone(true);
      // Refetch to pick up the processing candidate
      const listRes = await candidatesApi.list({ limit: 100 });
      const seen = new Set<string>();
      setAllCandidates(listRes.data.candidates.filter((c: Candidate) => {
        if (seen.has(c.id)) return false;
        seen.add(c.id);
        return true;
      }));
    } catch (e) {
      console.error(e);
      setUploadFiles([{ name: file.name, size: formatFileSize(file.size), status: "error" }]);
      setError("Failed to upload resume. Please try again.");
      setUploadApiDone(true);
    }
  }, []);

  // Multi-file upload
  const handleBulkUpload = useCallback(async (files: File[]) => {
    const validFiles = files.filter((f) => f.name.match(/\.(pdf|docx|txt)$/i));
    if (validFiles.length === 0) {
      setError("No valid files. Supported formats: PDF, DOCX, TXT");
      return;
    }

    setUploadApiDone(false);
    setUploadFiles(validFiles.map((f) => ({ name: f.name, size: formatFileSize(f.size), status: "uploading" as const })));
    setError(null);

    try {
      const res = await candidatesApi.bulkUpload(validFiles);
      const failedNames = new Set((res.data.failed || []).map((f: any) => f.filename));
      setUploadFiles(validFiles.map((f) => ({
        name: f.name,
        size: formatFileSize(f.size),
        status: failedNames.has(f.name) ? "error" as const : "done" as const,
      })));
      setUploadApiDone(true);

      // Refetch to pick up processing candidates
      const listRes = await candidatesApi.list({ limit: 100 });
      const seen = new Set<string>();
      setAllCandidates(
        listRes.data.candidates.filter((c: Candidate) => {
          if (seen.has(c.id)) return false;
          seen.add(c.id);
          return true;
        })
      );

      if (res.data.total_failed > 0) {
        const failedNamesList = res.data.failed.map((f: any) => f.filename).join(", ");
        setError(`${res.data.total_created} uploaded. ${res.data.total_failed} failed: ${failedNamesList}`);
      }
    } catch (e) {
      console.error(e);
      setError("Bulk upload failed. Please try again.");
      setUploadApiDone(true);
    }
  }, []);

  // Single delete with confirm dialog
  const handleDeleteSingle = useCallback((e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDeleteTargetId(id);
    setConfirmOpen(true);
  }, []);

  // Confirm delete handler
  const handleConfirmDelete = useCallback(async () => {
    setConfirmLoading(true);
    try {
      if (deleteTargetId) {
        await candidatesApi.delete(deleteTargetId);
        setAllCandidates((prev) => prev.filter((c) => c.id !== deleteTargetId));
      } else {
        const ids = selectedIds;
        setAllCandidates((prev) => prev.filter((c) => !ids.includes(c.id)));
        try {
          const res = await candidatesApi.bulkDelete(ids);
          if (res.data.failed_ids?.length > 0) {
            setError(`Deleted ${res.data.deleted_count} of ${ids.length}. ${res.data.failed_ids.length} failed.`);
            const listRes = await candidatesApi.list({ limit: 100 });
            setAllCandidates(listRes.data.candidates);
          }
        } catch (err) {
          const listRes = await candidatesApi.list({ limit: 100 });
          setAllCandidates(listRes.data.candidates);
          throw err;
        }
        clear();
      }
    } catch (err) {
      console.error(err);
      setError("Delete failed. Please try again.");
    } finally {
      setConfirmLoading(false);
      setConfirmOpen(false);
      setDeleteTargetId(null);
    }
  }, [deleteTargetId, selectedIds, clear]);

  const handleCancelDelete = useCallback(() => {
    setConfirmOpen(false);
    setDeleteTargetId(null);
  }, []);

  // Drag and drop
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 1 && files[0]) {
      handleUpload(files[0]);
    } else if (files.length > 1) {
      handleBulkUpload(files);
    }
  }, [handleUpload, handleBulkUpload]);

  // Confirm dialog text
  const confirmTitle = deleteTargetId
    ? "Delete candidate?"
    : `Delete ${count} candidate${count !== 1 ? "s" : ""}?`;
  const confirmDesc = deleteTargetId
    ? "This will permanently remove the candidate and all associated analyses. This cannot be undone."
    : `This will permanently remove ${count} candidate${count !== 1 ? "s" : ""} and all associated analyses. This cannot be undone.`;

  // Upload card status text
  const uploadCardStatus = !uploadApiDone
    ? `Uploading ${uploadFiles.length} file${uploadFiles.length > 1 ? "s" : ""}...`
    : processingCount > 0
    ? `Processing ${processingCount} resume${processingCount !== 1 ? "s" : ""}...`
    : "Complete";

  const uploadCardIcon = !uploadApiDone || processingCount > 0
    ? <Loader2 size={16} className="animate-spin text-blue-500" />
    : <CheckCircle2 size={16} className="text-emerald-500" />;

  return (
    <div className="page-container animate-fade-in">
      <ConfirmDialog
        open={confirmOpen}
        title={confirmTitle}
        description={confirmDesc}
        actionText={deleteTargetId ? "Delete" : `Delete ${count}`}
        danger
        loading={confirmLoading}
        onConfirm={handleConfirmDelete}
        onCancel={handleCancelDelete}
      />

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-text-primary tracking-tight">
            Candidates
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            {readyCandidates.length} candidate{readyCandidates.length !== 1 ? "s" : ""} in your pipeline
          </p>
        </div>
        <label className="btn-primary cursor-pointer group">
          <Upload size={16} className="group-hover:scale-110 transition-transform" />
          Upload Resumes
          <input
            type="file"
            accept=".pdf,.docx,.txt"
            multiple
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              if (files.length === 1 && files[0]) handleUpload(files[0]);
              else if (files.length > 1) handleBulkUpload(files);
              e.target.value = "";
            }}
          />
        </label>
      </div>

      {/* ── Search ───────────────────────────────────────────────── */}
      <div className="relative mb-6">
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-text-tertiary" size={16} />
        <input
          type="text"
          placeholder="Search by name, role, or company..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input-field pl-11"
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-4 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-primary transition-colors"
          >
            <XCircle size={14} />
          </button>
        )}
      </div>

      <BulkActionBar
        count={count}
        totalCount={readyCandidates.length}
        isAllSelected={isAllSelected}
        onToggleAll={toggleAll}
        onDelete={() => { setDeleteTargetId(null); setConfirmOpen(true); }}
        onClear={clear}
      />

      {/* ── Upload + Processing Card (unified) ────────────────────── */}
      {showUploadCard && (
        <div className="mb-6 p-5 bg-blue-50/80 rounded-xl border border-blue-100 animate-slide-up">
          <div className="flex items-center gap-3 mb-3">
            {uploadCardIcon}
            <p className="text-sm font-medium text-blue-800">{uploadCardStatus}</p>
          </div>
          <div className="space-y-1.5">
            {uploadFiles.map((f, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5 animate-fade-in" style={{ animationDelay: `${i * 60}ms` }}>
                <FileText size={13} className="text-blue-400 flex-shrink-0" />
                <span className="text-xs text-blue-700 flex-1 truncate">{f.name}</span>
                <span className="text-2xs text-blue-400">{f.size}</span>
                {f.status === "uploading" && <Loader2 size={11} className="animate-spin text-blue-500" />}
                {f.status === "done" && <CheckCircle2 size={11} className="text-emerald-500" />}
                {f.status === "error" && <XCircle size={11} className="text-red-500" />}
                {f.status === "pending" && <Clock size={11} className="text-blue-300" />}
              </div>
            ))}
          </div>
          {/* Processing progress bar */}
          {uploadApiDone && processingCount > 0 && (
            <div className="mt-3 pt-3 border-t border-blue-100">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-2xs text-blue-600">Parsing resumes...</span>
                <span className="text-2xs text-blue-500">
                  {uploadFiles.length - processingCount}/{uploadFiles.length}
                </span>
              </div>
              <div className="h-1 bg-blue-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-700 ease-out"
                  style={{ width: `${((uploadFiles.length - processingCount) / uploadFiles.length) * 100}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Error ──────────────────────────────────────────────── */}
      {error && (
        <div className="mb-6 p-4 bg-red-50 rounded-xl border border-red-100 flex items-center justify-between animate-slide-up">
          <div className="flex items-center gap-2">
            <AlertCircle size={15} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 text-sm font-medium ml-4">
            Dismiss
          </button>
        </div>
      )}

      {/* ── List ─────────────────────────────────────────────────── */}
      {loading ? (
        <div className="glass-card-solid overflow-hidden">
          {[1,2,3,4,5].map((i) => (
            <div key={i} className="flex items-center gap-5 px-5 py-3.5 border-b border-surface-border last:border-0">
              <div className="w-4 h-4 shimmer rounded" />
              <div className="w-11 h-11 shimmer rounded-full" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-40 shimmer rounded" />
                <div className="h-3 w-56 shimmer rounded" />
              </div>
              <div className="h-6 w-16 shimmer rounded-lg" />
            </div>
          ))}
        </div>
      ) : readyCandidates.length === 0 ? (
        <div
          className={`glass-card-solid p-16 text-center transition-all duration-300 ${dragOver ? "drop-zone-active" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <div className={`w-16 h-16 rounded-xl bg-surface-tertiary flex items-center justify-center mx-auto mb-4 transition-transform duration-200 ${dragOver ? "scale-110 animate-float" : ""}`}>
            <Users size={28} className="text-text-tertiary" />
          </div>
          <p className="text-base font-medium text-text-primary">
            {processingCount > 0 ? "Processing resumes..." : "No candidates yet"}
          </p>
          <p className="text-sm text-text-tertiary mt-1.5 mb-2">
            {processingCount > 0
              ? "Candidates will appear here as they're processed"
              : "Upload resumes to add candidates to your pipeline."}
          </p>
          {processingCount === 0 && (
            <>
              <div className="flex items-center justify-center gap-2 mb-6">
                <FilePlus size={14} className="text-brand-400" />
                <span className="text-xs font-medium text-brand-500">
                  Drop multiple files at once for bulk upload
                </span>
              </div>
              <label className="btn-primary cursor-pointer">
                <Upload size={16} />
                Upload Resumes
                <input
                  type="file"
                  accept=".pdf,.docx,.txt"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const files = Array.from(e.target.files || []);
                    if (files.length === 1 && files[0]) handleUpload(files[0]);
                    else if (files.length > 1) handleBulkUpload(files);
                    e.target.value = "";
                  }}
                />
              </label>
            </>
          )}
        </div>
      ) : (
        <div
          className={`glass-card-solid divide-y divide-surface-border transition-all duration-300 overflow-hidden ${dragOver ? "ring-2 ring-brand-400 shadow-glass" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {dragOver && (
            <div className="px-5 py-3 bg-brand-50 border-b border-brand-100 flex items-center gap-2 animate-fade-in">
              <Upload size={14} className="text-brand-500" />
              <span className="text-xs font-medium text-brand-600">Drop to upload more resumes</span>
            </div>
          )}

          <div className="px-5 py-3 flex items-center justify-between bg-surface-secondary/50">
            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={isAllSelected}
                onChange={toggleAll}
                className="w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-brand-500 cursor-pointer"
              />
              <span className="text-xs text-text-tertiary">
                {isAllSelected ? "Deselect all" : "Select all"}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <FilePlus size={12} className="text-text-tertiary" />
              <span className="text-2xs text-text-tertiary">Drag files here to add more resumes</span>
            </div>
          </div>

          {readyCandidates.map((c, i) => (
            <div
              key={c.id}
              className={`flex items-center gap-3 px-5 py-3.5 hover:bg-surface-hover transition-all duration-150 group stagger-item ${isSelected(c.id) ? "bg-brand-50/40" : ""}`}
              style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}
            >
              <input
                type="checkbox"
                checked={isSelected(c.id)}
                onChange={(e) => {
                  e.stopPropagation();
                  toggleItem(c.id);
                }}
                className="w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-brand-500 cursor-pointer flex-shrink-0"
              />

              <Link
                to={`/candidates/${c.id}`}
                className="flex items-center gap-5 flex-1 min-w-0"
              >
                <div className="w-9 h-9 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0 group-hover:ring-2 group-hover:ring-brand-200 transition-all">
                  <span className="text-base font-semibold text-brand-500">
                    {c.name.split(" ").map(n => n[0]).join("").slice(0, 2).toUpperCase()}
                  </span>
                </div>

                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-text-primary group-hover:text-brand-500 transition-colors">{c.name}</p>
                  <div className="flex items-center gap-4 mt-1">
                    {c.processing_status === "failed" ? (
                      <span className="flex items-center gap-1.5 text-xs text-red-500">
                        <AlertCircle size={11} />
                        Processing failed
                      </span>
                    ) : (
                      <>
                        {c.current_role && (
                          <span className="text-xs text-text-secondary">{c.current_role}</span>
                        )}
                        {c.current_company && (
                          <span className="flex items-center gap-1 text-xs text-text-tertiary">
                            <Building2 size={11} />
                            {c.current_company}
                          </span>
                        )}
                        {c.location && (
                          <span className="flex items-center gap-1 text-xs text-text-tertiary">
                            <MapPin size={11} />
                            {c.location}
                          </span>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </Link>

              <div className="flex items-center gap-4 flex-shrink-0">
                {c.years_experience !== undefined && c.years_experience !== null && (
                  <span className="text-xs text-text-tertiary bg-surface-tertiary px-2.5 py-1 rounded-lg">
                    {c.years_experience < 1 ? "<1" : Math.round(c.years_experience)}y exp
                  </span>
                )}
                {c.education_level && (
                  <span className="text-xs text-text-tertiary bg-surface-tertiary px-2.5 py-1 rounded-lg">
                    {c.education_level}
                  </span>
                )}
                <span className="flex items-center gap-1 text-xs text-text-tertiary">
                  <Calendar size={11} />
                  {new Date(c.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </span>
                <button
                  onClick={(e) => handleDeleteSingle(e, c.id)}
                  className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-text-tertiary hover:text-red-500 hover:bg-red-50 transition-all"
                  title="Delete candidate"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Bottom-right Intelligence Profile Toast ────────────────── */}
      {profilesPendingCount > 0 && (
        <div className="fixed bottom-6 right-6 z-50 animate-slide-up">
          <div className="flex items-center gap-3 px-4 py-3 bg-white/95 backdrop-blur-sm rounded-xl shadow-lg border border-gray-100">
            <div className="relative">
              <Sparkles size={14} className="text-brand-500" />
              <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-brand-400 rounded-full animate-pulse" />
            </div>
            <span className="text-xs text-text-secondary">
              Generating intelligence profile{profilesPendingCount !== 1 ? "s" : ""}...
            </span>
            <div className="w-12 h-1 bg-gray-100 rounded-full overflow-hidden">
              <div className="h-full bg-brand-400 rounded-full animate-indeterminate" />
            </div>
          </div>
        </div>
      )}

      {/* Indeterminate progress bar animation */}
      <style>{`
        @keyframes indeterminate {
          0% { transform: translateX(-100%); width: 40%; }
          50% { transform: translateX(100%); width: 60%; }
          100% { transform: translateX(300%); width: 40%; }
        }
        .animate-indeterminate {
          animation: indeterminate 1.5s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
}
