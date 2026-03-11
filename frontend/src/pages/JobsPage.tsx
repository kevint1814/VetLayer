import { useEffect, useState, useCallback } from "react";
import { Plus, Briefcase, MapPin, X, Trash2, Loader2, ClipboardPaste, Star, CheckCircle2, AlertCircle, Pencil, Save, RotateCcw } from "lucide-react";
import { jobsApi } from "@/services/api";
import type { Job } from "@/types";
import { useMultiSelect } from "@/hooks/useMultiSelect";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import BulkActionBar from "@/components/common/BulkActionBar";

type FormMode = "smart" | "manual";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formMode, setFormMode] = useState<FormMode>("smart");
  const [error, setError] = useState<string | null>(null);
  const [justCreated, setJustCreated] = useState<string | null>(null);

  // Multi-select
  const { selectedIds, toggleItem, toggleAll, clear, isSelected, isAllSelected, isSomeSelected, count } =
    useMultiSelect(jobs);

  // Confirm dialog
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);

  // Editing state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editData, setEditData] = useState<{
    title: string; company: string; location: string; remote_policy: string;
    description: string; required_skills: any[]; preferred_skills: any[];
  }>({ title: "", company: "", location: "", remote_policy: "hybrid", description: "", required_skills: [], preferred_skills: [] });
  const [editSaving, setEditSaving] = useState(false);
  const [reparsing, setReparsing] = useState(false);
  const [reparseText, setReparseText] = useState("");
  const [showReparse, setShowReparse] = useState(false);

  // Shared form state
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [remotePolicy, setRemotePolicy] = useState("hybrid");

  // Smart mode — raw paste
  const [rawRequirements, setRawRequirements] = useState("");

  // Manual mode — structured inputs
  const [description, setDescription] = useState("");
  const [skillInputs, setSkillInputs] = useState([{ skill: "", min_depth: 3, weight: 0.8 }]);

  useEffect(() => {
    jobsApi.list().then((r) => setJobs(r.data.jobs)).catch(console.error).finally(() => setLoading(false));
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "a" && !loading && jobs.length > 0 && !showForm) {
        if ((e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA") return;
        e.preventDefault();
        toggleAll();
      }
      if (e.key === "Delete" && isSomeSelected) {
        e.preventDefault();
        setDeleteTargetId(null);
        setConfirmOpen(true);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [loading, jobs.length, isSomeSelected, toggleAll, showForm]);

  const addSkillRow = () => setSkillInputs([...skillInputs, { skill: "", min_depth: 3, weight: 0.8 }]);
  const removeSkillRow = (i: number) => setSkillInputs(skillInputs.filter((_, idx) => idx !== i));

  const resetForm = () => {
    setTitle(""); setCompany(""); setLocation(""); setDescription("");
    setRawRequirements(""); setError(null);
    setSkillInputs([{ skill: "", min_depth: 3, weight: 0.8 }]);
  };

  const startEditing = (j: Job) => {
    setEditingId(j.id);
    setEditData({
      title: j.title,
      company: j.company || "",
      location: j.location || "",
      remote_policy: j.remote_policy || "hybrid",
      description: j.description || "",
      required_skills: j.required_skills || [],
      preferred_skills: j.preferred_skills || [],
    });
    setShowReparse(false);
    setReparseText("");
  };

  const cancelEditing = () => {
    setEditingId(null);
    setShowReparse(false);
    setReparseText("");
    setError(null);
  };

  const saveEdit = async () => {
    if (!editingId || !editData.title) return;
    setEditSaving(true);
    setError(null);
    try {
      const res = await jobsApi.update(editingId, {
        title: editData.title,
        company: editData.company || undefined,
        location: editData.location || undefined,
        remote_policy: editData.remote_policy || undefined,
        description: editData.description || undefined,
        required_skills: editData.required_skills.length > 0 ? editData.required_skills : undefined,
        preferred_skills: editData.preferred_skills.length > 0 ? editData.preferred_skills : undefined,
      });
      setJobs((prev) => prev.map((j) => (j.id === editingId ? res.data : j)));
      setEditingId(null);
      setJustCreated(editingId); // reuse the highlight animation
      setTimeout(() => setJustCreated(null), 2000);
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || "Failed to update job.");
    } finally {
      setEditSaving(false);
    }
  };

  const handleReparse = async () => {
    if (!editingId || !reparseText.trim()) return;
    setReparsing(true);
    setError(null);
    try {
      const res = await jobsApi.reparse(editingId, reparseText);
      setJobs((prev) => prev.map((j) => (j.id === editingId ? res.data : j)));
      setEditData((prev) => ({
        ...prev,
        description: res.data.description,
        required_skills: res.data.required_skills || [],
        preferred_skills: res.data.preferred_skills || [],
      }));
      setShowReparse(false);
      setReparseText("");
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || "Failed to re-parse requirements.");
    } finally {
      setReparsing(false);
    }
  };

  const removeEditSkill = (type: "required" | "preferred", idx: number) => {
    if (type === "required") {
      setEditData((prev) => ({ ...prev, required_skills: prev.required_skills.filter((_, i) => i !== idx) }));
    } else {
      setEditData((prev) => ({ ...prev, preferred_skills: prev.preferred_skills.filter((_, i) => i !== idx) }));
    }
  };

  const createJobSmart = async () => {
    if (!title || !rawRequirements.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const res = await jobsApi.createSmart({
        title,
        company: company || undefined,
        location: location || undefined,
        remote_policy: remotePolicy || undefined,
        raw_requirements: rawRequirements,
      });
      setJobs((prev) => [res.data, ...prev]);
      setJustCreated(res.data.id);
      setTimeout(() => setJustCreated(null), 2000);
      setShowForm(false);
      resetForm();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || "Failed to parse requirements. Try again.");
      console.error(err);
    } finally {
      setSaving(false);
    }
  };

  const createJobManual = async () => {
    if (!title || !description) return;
    setSaving(true);
    setError(null);
    try {
      const required = skillInputs
        .filter((s) => s.skill.trim())
        .map((s) => ({ skill: s.skill.trim(), min_depth: s.min_depth, weight: s.weight }));

      const res = await jobsApi.create({
        title,
        company: company || undefined,
        description,
        required_skills: required.length ? required : undefined,
        location: location || undefined,
        remote_policy: remotePolicy || undefined,
      });
      setJobs((prev) => [res.data, ...prev]);
      setJustCreated(res.data.id);
      setTimeout(() => setJustCreated(null), 2000);
      setShowForm(false);
      resetForm();
    } catch (err: unknown) {
      const axErr = err as { response?: { data?: { detail?: string } } };
      setError(axErr?.response?.data?.detail || "Failed to create job. Please try again.");
      console.error(err);
    } finally {
      setSaving(false);
    }
  };

  // Single delete with confirm dialog
  const handleDeleteSingle = useCallback((id: string) => {
    setDeleteTargetId(id);
    setConfirmOpen(true);
  }, []);

  // Confirm delete handler
  const handleConfirmDelete = useCallback(async () => {
    setConfirmLoading(true);
    try {
      if (deleteTargetId) {
        await jobsApi.delete(deleteTargetId);
        setJobs((prev) => prev.filter((j) => j.id !== deleteTargetId));
      } else {
        const ids = selectedIds;
        setJobs((prev) => prev.filter((j) => !ids.includes(j.id)));
        try {
          const res = await jobsApi.bulkDelete(ids);
          if (res.data.failed_ids?.length > 0) {
            setError(`Deleted ${res.data.deleted_count} of ${ids.length}. ${res.data.failed_ids.length} failed.`);
            const listRes = await jobsApi.list();
            setJobs(listRes.data.jobs);
          }
        } catch (err) {
          const listRes = await jobsApi.list();
          setJobs(listRes.data.jobs);
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

  const confirmTitle = deleteTargetId
    ? "Delete job?"
    : `Delete ${count} job${count !== 1 ? "s" : ""}?`;
  const confirmDesc = deleteTargetId
    ? "This will permanently remove the job and all associated analyses. This cannot be undone."
    : `This will permanently remove ${count} job${count !== 1 ? "s" : ""} and all associated analyses. This cannot be undone.`;

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
        onCancel={() => { setConfirmOpen(false); setDeleteTargetId(null); }}
      />

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-text-primary tracking-tight">Jobs</h1>
          <p className="text-sm text-text-secondary mt-1">
            {jobs.length} job description{jobs.length !== 1 ? "s" : ""}
          </p>
        </div>
        <button className="btn-primary group" onClick={() => setShowForm(true)}>
          <Plus size={16} className="group-hover:rotate-90 transition-transform duration-200" />
          New Job
        </button>
      </div>

      <BulkActionBar
        count={count}
        totalCount={jobs.length}
        isAllSelected={isAllSelected}
        onToggleAll={toggleAll}
        onDelete={() => { setDeleteTargetId(null); setConfirmOpen(true); }}
        onClear={clear}
      />

      {/* ── Create form ──────────────────────────────────────────── */}
      {showForm && (
        <div className="glass-card-solid p-6 mb-6 animate-slide-up">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-base font-semibold text-text-primary">Create Job Description</h2>
            <button onClick={() => { setShowForm(false); resetForm(); }} className="text-text-tertiary hover:text-text-primary transition-colors">
              <X size={18} />
            </button>
          </div>

          {/* Mode toggle */}
          <div className="flex gap-2 mb-6">
            <button
              onClick={() => setFormMode("smart")}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                formMode === "smart"
                  ? "bg-brand-500 text-white shadow-sm"
                  : "bg-surface-tertiary text-text-secondary hover:bg-surface-hover"
              }`}
            >
              Quick Create
            </button>
            <button
              onClick={() => setFormMode("manual")}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                formMode === "manual"
                  ? "bg-brand-500 text-white shadow-sm"
                  : "bg-surface-tertiary text-text-secondary hover:bg-surface-hover"
              }`}
            >
              Manual Entry
            </button>
          </div>

          <div className="space-y-5">
            {/* Common fields */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">Job Title *</label>
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Frontend Developer" className="input-field" />
              </div>
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">Company</label>
                <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="e.g. Acme Inc" className="input-field" />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">Location</label>
                <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="e.g. San Francisco, CA" className="input-field" />
              </div>
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">Remote Policy</label>
                <select value={remotePolicy} onChange={(e) => setRemotePolicy(e.target.value)} className="input-field">
                  <option value="remote">Remote</option>
                  <option value="hybrid">Hybrid</option>
                  <option value="onsite">On-site</option>
                </select>
              </div>
            </div>

            {/* SMART MODE */}
            {formMode === "smart" && (
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">
                  Paste requirements from career page *
                </label>
                <div className="relative">
                  <textarea
                    value={rawRequirements}
                    onChange={(e) => setRawRequirements(e.target.value)}
                    placeholder={"Paste the \"You should apply if\" or \"Requirements\" section here.\n\nExample:\n* 1 to 3 Years of experience\n* Knowledge in React\n* Hands-on experience in HTML, CSS\n* Familiarity with AWS (Nice to Have)\n\nVetLayer will automatically extract skills and estimate depth levels."}
                    rows={8}
                    className="input-field resize-none font-mono text-xs leading-relaxed"
                  />
                  {!rawRequirements && (
                    <div className="absolute top-3 right-3">
                      <ClipboardPaste size={18} className="text-text-tertiary/40" />
                    </div>
                  )}
                </div>
                <p className="text-2xs text-text-tertiary mt-1.5">
                  Skill names, required depth levels (1-5), and required vs. nice-to-have categories will be automatically extracted.
                </p>
              </div>
            )}

            {/* MANUAL MODE */}
            {formMode === "manual" && (
              <>
                <div>
                  <label className="block text-xs font-medium text-text-secondary mb-1.5">Description *</label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Describe the role, responsibilities, and what you're looking for..."
                    rows={4}
                    className="input-field resize-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-text-secondary mb-3">Required Skills</label>
                  <div className="space-y-2">
                    {skillInputs.map((s, i) => (
                      <div key={i} className="flex items-center gap-3 animate-fade-in">
                        <input
                          value={s.skill}
                          onChange={(e) => {
                            const next = [...skillInputs];
                            next[i]!.skill = e.target.value;
                            setSkillInputs(next);
                          }}
                          placeholder="Skill name (e.g. Python)"
                          className="input-field flex-1"
                        />
                        <div className="flex items-center gap-1.5">
                          <span className="text-2xs text-text-tertiary whitespace-nowrap">Min depth:</span>
                          <select
                            value={s.min_depth}
                            onChange={(e) => {
                              const next = [...skillInputs];
                              next[i]!.min_depth = Number(e.target.value);
                              setSkillInputs(next);
                            }}
                            className="input-field w-16 text-center"
                          >
                            {[1,2,3,4,5].map(d => <option key={d} value={d}>{d}</option>)}
                          </select>
                        </div>
                        {skillInputs.length > 1 && (
                          <button onClick={() => removeSkillRow(i)} className="text-text-tertiary hover:text-status-danger transition-colors">
                            <X size={16} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  <button onClick={addSkillRow} className="text-xs text-brand-500 hover:text-brand-600 font-medium mt-2 transition-colors">
                    + Add skill
                  </button>
                </div>
              </>
            )}

            {/* Error */}
            {error && showForm && (
              <div className="p-4 bg-red-50 rounded-xl border border-red-100 flex items-center gap-2 animate-slide-up">
                <AlertCircle size={14} className="text-red-500 flex-shrink-0" />
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={formMode === "smart" ? createJobSmart : createJobManual}
                disabled={saving || !title || (formMode === "smart" ? !rawRequirements.trim() : !description)}
                className="btn-primary disabled:opacity-50"
              >
                {saving ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Creating...
                  </>
                ) : (
                  <>Create Job</>
                )}
              </button>
              <button onClick={() => { setShowForm(false); resetForm(); }} className="btn-secondary">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Error (outside form) */}
      {error && !showForm && (
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

      {/* ── Job list ─────────────────────────────────────────────── */}
      {loading ? (
        <div className="space-y-4">
          {[1,2,3].map((i) => (
            <div key={i} className="glass-card-solid p-6">
              <div className="flex items-start gap-4">
                <div className="w-4 h-4 shimmer rounded mt-1" />
                <div className="flex-1 space-y-3">
                  <div className="h-5 w-48 shimmer rounded-lg" />
                  <div className="h-3 w-36 shimmer rounded-lg" />
                  <div className="flex gap-2 mt-4">
                    {[1,2,3].map(j => <div key={j} className="h-6 w-20 shimmer rounded-lg" />)}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : jobs.length === 0 && !showForm ? (
        <div className="glass-card-solid p-16 text-center">
          <div className="w-16 h-16 rounded-xl bg-surface-tertiary flex items-center justify-center mx-auto mb-4">
            <Briefcase size={28} className="text-text-tertiary" />
          </div>
          <p className="text-base font-medium text-text-primary">No job descriptions yet</p>
          <p className="text-sm text-text-tertiary mt-1.5 mb-2">
            Create a job to start matching candidates against requirements.
          </p>
          <p className="text-xs text-brand-500 font-medium mb-6">
            Tip: Use "Smart Paste" to extract skills automatically from any job posting
          </p>
          <button className="btn-primary" onClick={() => setShowForm(true)}>
            <Plus size={16} />
            Create First Job
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {jobs.map((j, i) => {
            const isEditing = editingId === j.id;

            return (
              <div
                key={j.id}
                className={`glass-card-solid p-5 transition-all duration-300 stagger-item ${
                  isEditing ? "ring-2 ring-brand-400 shadow-glass" :
                  isSelected(j.id) ? "ring-2 ring-brand-300 bg-brand-50/30" :
                  justCreated === j.id ? "ring-2 ring-emerald-300 bg-emerald-50/20" : "hover-lift"
                }`}
                style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
              >
                {isEditing ? (
                  /* ── Edit mode ────────────────────────────────────── */
                  <div className="space-y-4 animate-fade-in">
                    <div className="flex items-center justify-between mb-2">
                      <h3 className="text-sm font-semibold text-brand-500">Editing Job</h3>
                      <button onClick={cancelEditing} className="text-text-tertiary hover:text-text-primary transition-colors">
                        <X size={16} />
                      </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="block text-2xs font-medium text-text-tertiary mb-1">Title *</label>
                        <input value={editData.title} onChange={(e) => setEditData({ ...editData, title: e.target.value })} className="input-field text-sm" />
                      </div>
                      <div>
                        <label className="block text-2xs font-medium text-text-tertiary mb-1">Company</label>
                        <input value={editData.company} onChange={(e) => setEditData({ ...editData, company: e.target.value })} className="input-field text-sm" />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="block text-2xs font-medium text-text-tertiary mb-1">Location</label>
                        <input value={editData.location} onChange={(e) => setEditData({ ...editData, location: e.target.value })} className="input-field text-sm" />
                      </div>
                      <div>
                        <label className="block text-2xs font-medium text-text-tertiary mb-1">Remote Policy</label>
                        <select value={editData.remote_policy} onChange={(e) => setEditData({ ...editData, remote_policy: e.target.value })} className="input-field text-sm">
                          <option value="remote">Remote</option>
                          <option value="hybrid">Hybrid</option>
                          <option value="onsite">On-site</option>
                        </select>
                      </div>
                    </div>

                    <div>
                      <label className="block text-2xs font-medium text-text-tertiary mb-1">Description</label>
                      <textarea
                        value={editData.description}
                        onChange={(e) => setEditData({ ...editData, description: e.target.value })}
                        rows={3}
                        className="input-field text-sm resize-none"
                      />
                    </div>

                    {/* Editable skills */}
                    {editData.required_skills.length > 0 && (
                      <div>
                        <p className="text-2xs text-text-tertiary uppercase tracking-wider mb-2">Required Skills</p>
                        <div className="flex flex-wrap gap-2">
                          {editData.required_skills.map((s: any, idx: number) => (
                            <span key={idx} className="inline-flex items-center gap-1.5 text-xs font-medium bg-brand-50 text-brand-500 px-2.5 py-1 rounded-lg group">
                              {s.skill}
                              <span className="text-brand-300">·</span>
                              <span className="text-brand-400">depth {s.min_depth}+</span>
                              <button
                                onClick={() => removeEditSkill("required", idx)}
                                className="ml-0.5 text-brand-300 hover:text-red-500 transition-colors"
                              >
                                <X size={10} />
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {editData.preferred_skills.length > 0 && (
                      <div>
                        <p className="text-2xs text-text-tertiary uppercase tracking-wider mb-2">Nice to Have</p>
                        <div className="flex flex-wrap gap-2">
                          {editData.preferred_skills.map((s: any, idx: number) => (
                            <span key={idx} className="inline-flex items-center gap-1.5 text-xs font-medium bg-amber-50 text-amber-600 px-2.5 py-1 rounded-lg">
                              <Star size={10} />
                              {s.skill}
                              <button
                                onClick={() => removeEditSkill("preferred", idx)}
                                className="ml-0.5 text-amber-400 hover:text-red-500 transition-colors"
                              >
                                <X size={10} />
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Re-parse skills option */}
                    {!showReparse ? (
                      <button
                        onClick={() => setShowReparse(true)}
                        className="inline-flex items-center gap-1.5 text-xs text-brand-500 hover:text-brand-600 font-medium transition-colors"
                      >
                        <RotateCcw size={12} />
                        Re-parse skills from new requirements text
                      </button>
                    ) : (
                      <div className="p-4 bg-brand-50/50 rounded-xl border border-brand-100 space-y-3 animate-fade-in">
                        <div className="flex items-center justify-between">
                          <p className="text-xs font-medium text-brand-600">Paste new requirements — skills will be re-extracted automatically</p>
                          <button onClick={() => { setShowReparse(false); setReparseText(""); }} className="text-brand-400 hover:text-brand-600">
                            <X size={14} />
                          </button>
                        </div>
                        <textarea
                          value={reparseText}
                          onChange={(e) => setReparseText(e.target.value)}
                          placeholder="Paste updated requirements here..."
                          rows={4}
                          className="input-field text-xs font-mono resize-none"
                        />
                        <button
                          onClick={handleReparse}
                          disabled={reparsing || !reparseText.trim()}
                          className="btn-primary text-xs disabled:opacity-50"
                        >
                          {reparsing ? (
                            <><Loader2 size={12} className="animate-spin" /> Re-parsing...</>
                          ) : (
                            <><RotateCcw size={12} /> Re-parse Requirements</>
                          )}
                        </button>
                      </div>
                    )}

                    {/* Save / Cancel */}
                    <div className="flex items-center gap-3 pt-2 border-t border-surface-border">
                      <button onClick={saveEdit} disabled={editSaving || !editData.title} className="btn-primary text-sm disabled:opacity-50">
                        {editSaving ? <><Loader2 size={14} className="animate-spin" /> Saving...</> : <><Save size={14} /> Save Changes</>}
                      </button>
                      <button onClick={cancelEditing} className="btn-secondary text-sm">Cancel</button>
                    </div>
                  </div>
                ) : (
                  /* ── View mode ────────────────────────────────────── */
                  <>
                    <div className="flex items-start justify-between">
                      <div className="flex items-start gap-4">
                        <input
                          type="checkbox"
                          checked={isSelected(j.id)}
                          onChange={() => toggleItem(j.id)}
                          className="w-4 h-4 rounded border-gray-300 text-brand-500 focus:ring-brand-500 cursor-pointer mt-1"
                        />
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="text-base font-semibold text-text-primary">{j.title}</h3>
                            {justCreated === j.id && (
                              <span className="inline-flex items-center gap-1 text-2xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium animate-fade-in">
                                <CheckCircle2 size={10} /> Just created
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 mt-1.5">
                            {j.company && (
                              <span className="flex items-center gap-1 text-xs text-text-secondary">
                                <Briefcase size={12} /> {j.company}
                              </span>
                            )}
                            {j.location && (
                              <span className="flex items-center gap-1 text-xs text-text-tertiary">
                                <MapPin size={12} /> {j.location}
                              </span>
                            )}
                            {j.remote_policy && (
                              <span className="text-xs text-text-tertiary bg-surface-tertiary px-2 py-0.5 rounded-md capitalize">
                                {j.remote_policy}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-text-tertiary mr-1">
                          {new Date(j.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        </span>
                        <button
                          onClick={() => startEditing(j)}
                          className="p-1.5 rounded-lg text-text-tertiary hover:text-brand-500 hover:bg-brand-50 transition-all"
                          title="Edit job"
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDeleteSingle(j.id)}
                          className="p-1.5 rounded-lg text-text-tertiary hover:text-red-500 hover:bg-red-50 transition-all"
                          title="Delete job"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {j.description && (
                      <p className="text-sm text-text-secondary mt-3 ml-8 line-clamp-2 leading-relaxed">
                        {j.description}
                      </p>
                    )}

                    {j.required_skills && j.required_skills.length > 0 && (
                      <div className="mt-4 ml-8">
                        <p className="text-2xs text-text-tertiary uppercase tracking-wider mb-2">Required</p>
                        <div className="flex flex-wrap gap-2">
                          {j.required_skills.map((s: any, idx: number) => (
                            <span key={idx} className="inline-flex items-center gap-1 text-xs font-medium bg-brand-50 text-brand-500 px-2.5 py-1 rounded-lg transition-transform hover:scale-105">
                              {s.skill}
                              <span className="text-brand-300">·</span>
                              <span className="text-brand-400">depth {s.min_depth}+</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {j.preferred_skills && j.preferred_skills.length > 0 && (
                      <div className="mt-3 ml-8">
                        <p className="text-2xs text-text-tertiary uppercase tracking-wider mb-2">Nice to have</p>
                        <div className="flex flex-wrap gap-2">
                          {j.preferred_skills.map((s: any, idx: number) => (
                            <span key={idx} className="inline-flex items-center gap-1 text-xs font-medium bg-amber-50 text-amber-600 px-2.5 py-1 rounded-lg transition-transform hover:scale-105">
                              <Star size={10} />
                              {s.skill}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
