import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../contexts/AuthContext";
import { adminApi } from "../services/api";
import {
  Users, Activity, BarChart3, Plus, Search, RotateCcw, UserX, UserCheck,
  Key, AlertCircle, CheckCircle2, Loader2, Eye, EyeOff, X, Shield, Clock, Building2,
} from "lucide-react";

/** Extract a readable error message from Axios errors (handles 422 validation arrays). */
function extractError(err: any, fallback = "Something went wrong."): string {
  const detail = err?.response?.data?.detail;
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
  }
  return fallback;
}

interface UserRecord {
  id: string;
  username: string;
  full_name: string;
  role: string;
  is_active: boolean;
  force_password_change: boolean;
  last_login_at: string | null;
  failed_login_attempts: number;
  created_at: string;
  company_id: string | null;
  company_name: string | null;
}

interface AuditEntry {
  id: string;
  username: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: string | null;
  ip_address: string | null;
  created_at: string;
}

interface PlatformStats {
  total_users: number;
  active_users: number;
  total_candidates: number;
  total_jobs: number;
  total_analyses: number;
  total_batch_runs: number;
  recent_logins_7d: number;
}

interface Company {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  user_count: number;
  created_at: string;
}

type Tab = "users" | "activity" | "companies" | "stats";

export default function AdminPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const [activeTab, setActiveTab] = useState<Tab>("users");

  const tabs = [
    { id: "users" as Tab, label: "User Management", icon: Users },
    { id: "activity" as Tab, label: "Activity Log", icon: Activity },
    ...(isSuperAdmin ? [{ id: "companies" as Tab, label: "Companies", icon: Building2 }] : []),
    { id: "stats" as Tab, label: "Platform Stats", icon: BarChart3 },
  ];

  return (
    <div className="page-container">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Admin Panel</h1>
          <p className="text-sm text-text-tertiary mt-0.5">Manage users, view activity, and monitor the platform</p>
        </div>
        <div className="flex items-center gap-1 p-1 rounded-lg bg-surface-tertiary">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                activeTab === id
                  ? "bg-white text-text-primary shadow-xs"
                  : "text-text-tertiary hover:text-text-secondary"
              }`}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "users" && <UserManagement />}
      {activeTab === "activity" && <ActivityLog />}
      {activeTab === "companies" && isSuperAdmin && <CompanyManagement />}
      {activeTab === "stats" && <StatsPanel />}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// USER MANAGEMENT TAB
// ════════════════════════════════════════════════════════════════════

function UserManagement() {
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [resetTarget, setResetTarget] = useState<UserRecord | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = {};
      if (search) params.search = search;
      if (filter === "active") params.status = "active";
      if (filter === "inactive") params.status = "inactive";
      if (filter === "pending") params.status = "pending";
      if (filter === "super_admin") params.role = "super_admin";
      if (filter === "company_admin") params.role = "company_admin";
      if (filter === "recruiter") params.role = "recruiter";
      const res = await adminApi.listUsers(params);
      setUsers(res.data.users);
      setTotal(res.data.total);
    } catch (err: any) {
      setActionError(extractError(err, "Failed to load users."));
    } finally {
      setLoading(false);
    }
  }, [search, filter]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleDeactivate = async (u: UserRecord) => {
    setActionLoading(u.id);
    setActionError(null);
    try {
      await adminApi.deactivateUser(u.id);
      await fetchUsers();
    } catch (err: any) {
      setActionError(extractError(err, `Failed to deactivate ${u.full_name}.`));
    }
    setActionLoading(null);
  };

  const handleReactivate = async (u: UserRecord) => {
    setActionLoading(u.id);
    setActionError(null);
    try {
      await adminApi.reactivateUser(u.id);
      await fetchUsers();
    } catch (err: any) {
      setActionError(extractError(err, `Failed to reactivate ${u.full_name}.`));
    }
    setActionLoading(null);
  };

  const formatDate = (d: string | null) => {
    if (!d) return "Never";
    return new Date(d).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };

  return (
    <>
      {/* Action error banner */}
      {actionError && (
        <div className="flex items-center justify-between gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
          <div className="flex items-center gap-2">
            <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{actionError}</p>
          </div>
          <button onClick={() => setActionError(null)} className="text-red-400 hover:text-red-600 p-0.5">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Search + filters + create button */}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-xs">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search users..."
            className="input-field w-full pl-9"
          />
        </div>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="input-field text-sm"
        >
          <option value="">All Users</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="pending">Pending First Login</option>
          <option value="super_admin">Super Admins</option>
          <option value="company_admin">Company Admins</option>
          <option value="recruiter">Recruiters</option>
        </select>
        <button onClick={() => setShowCreateModal(true)} className="btn-primary flex items-center gap-1.5">
          <Plus size={15} />
          Create User
        </button>
      </div>

      {/* User table */}
      <div className="glass-card-solid overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-surface-border">
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">User</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Role</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Status</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Last Login</th>
              <th className="text-right text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="text-center py-10 text-text-tertiary">
                <Loader2 size={20} className="animate-spin mx-auto mb-2" />Loading...
              </td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={5} className="text-center py-10 text-text-tertiary">No users found</td></tr>
            ) : (
              users.map((u) => (
                <tr key={u.id} className="border-b border-surface-border/50 hover:bg-surface-secondary/50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold ${
                        (u.role === "super_admin" || u.role === "company_admin") ? "bg-purple-100 text-purple-600" : "bg-brand-50 text-brand-500"
                      }`}>
                        {u.full_name.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <p className="text-sm font-medium text-text-primary">{u.full_name}</p>
                        <p className="text-xs text-text-tertiary">@{u.username}</p>
                        {u.company_name && (
                          <p className="text-xs text-text-tertiary">{u.company_name}</p>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                      (u.role === "super_admin" || u.role === "company_admin")
                        ? "bg-purple-100 text-purple-700"
                        : "bg-blue-50 text-blue-700"
                    }`}>
                      {(u.role === "super_admin" || u.role === "company_admin") && <Shield size={10} />}
                      {u.role === "super_admin" ? "Super Admin" : u.role === "company_admin" ? "Company Admin" : "Recruiter"}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    {!u.is_active ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700">Inactive</span>
                    ) : u.force_password_change ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700">Pending</span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">Active</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-text-tertiary">{formatDate(u.last_login_at)}</td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => setResetTarget(u)}
                        className="p-1.5 rounded-md text-text-tertiary hover:text-amber-600 hover:bg-amber-50 transition-colors"
                        title="Reset password"
                      >
                        <Key size={14} />
                      </button>
                      {u.is_active ? (
                        <button
                          onClick={() => handleDeactivate(u)}
                          disabled={actionLoading === u.id}
                          className="p-1.5 rounded-md text-text-tertiary hover:text-red-600 hover:bg-red-50 transition-colors"
                          title="Deactivate"
                        >
                          {actionLoading === u.id ? <Loader2 size={14} className="animate-spin" /> : <UserX size={14} />}
                        </button>
                      ) : (
                        <button
                          onClick={() => handleReactivate(u)}
                          disabled={actionLoading === u.id}
                          className="p-1.5 rounded-md text-text-tertiary hover:text-green-600 hover:bg-green-50 transition-colors"
                          title="Reactivate"
                        >
                          {actionLoading === u.id ? <Loader2 size={14} className="animate-spin" /> : <UserCheck size={14} />}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {total > 0 && (
          <div className="px-5 py-3 border-t border-surface-border text-xs text-text-tertiary">
            {total} user{total !== 1 ? "s" : ""} total
          </div>
        )}
      </div>

      {/* Create user modal */}
      {showCreateModal && (
        <CreateUserModal onClose={() => setShowCreateModal(false)} onCreated={fetchUsers} />
      )}

      {/* Reset password modal */}
      {resetTarget && (
        <ResetPasswordModal user={resetTarget} onClose={() => setResetTarget(null)} onReset={fetchUsers} />
      )}
    </>
  );
}

// ── Create User Modal ───────────────────────────────────────────────

function CreateUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { user: currentUser } = useAuth();
  const isSuperAdmin = currentUser?.role === "super_admin";
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("recruiter");
  const [companyId, setCompanyId] = useState<string>(currentUser?.company_id || "");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [usernameAvailable, setUsernameAvailable] = useState<boolean | null>(null);
  const [companies, setCompanies] = useState<{ id: string; name: string }[]>([]);

  // Load companies for super_admin dropdown
  useEffect(() => {
    if (isSuperAdmin) {
      adminApi.listCompanies().then((res) => setCompanies(res.data || [])).catch(() => {});
    }
  }, [isSuperAdmin]);

  // Company is required for non-super_admin roles
  const needsCompany = role !== "super_admin";

  // Check username availability
  useEffect(() => {
    if (username.length < 4) { setUsernameAvailable(null); return; }
    const timer = setTimeout(async () => {
      try {
        const res = await adminApi.checkUsername(username);
        setUsernameAvailable(res.data.available);
      } catch { setUsernameAvailable(null); }
    }, 400);
    return () => clearTimeout(timer);
  }, [username]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data: any = { username, full_name: fullName, password, role };
      if (isSuperAdmin && companyId) {
        data.company_id = companyId;
      } else if (!isSuperAdmin && currentUser?.company_id) {
        data.company_id = currentUser.company_id;
      }
      await adminApi.createUser(data);
      onCreated();
      onClose();
    } catch (err: any) {
      setError(extractError(err, "Failed to create user."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="glass-card-solid w-full max-w-md p-6 animate-fade-in">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-text-primary">Create New User</h3>
          <button onClick={onClose} className="p-1 rounded-md text-text-tertiary hover:text-text-secondary hover:bg-surface-tertiary">
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
            <AlertCircle size={16} className="text-red-500" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Full Name</label>
            <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)}
              className="input-field w-full" placeholder="John Smith" autoFocus disabled={loading} />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Username</label>
            <div className="relative">
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value.replace(/[^a-zA-Z0-9_]/g, ""))}
                className="input-field w-full pr-8" placeholder="john_smith" disabled={loading} />
              {usernameAvailable !== null && username.length >= 4 && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  {usernameAvailable ? (
                    <CheckCircle2 size={16} className="text-green-500" />
                  ) : (
                    <AlertCircle size={16} className="text-red-500" />
                  )}
                </div>
              )}
            </div>
            <p className="text-xs text-text-tertiary mt-1">Letters, numbers, and underscores only</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Temporary Password</label>
            <div className="relative">
              <input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                className="input-field w-full pr-10" placeholder="Min 8 chars, upper, lower, number, special" disabled={loading} />
              <button type="button" onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary" tabIndex={-1}>
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-xs text-text-tertiary mt-1">User will be required to change this on first login</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Role</label>
            <select value={role} onChange={(e) => setRole(e.target.value)} className="input-field w-full" disabled={loading}>
              {isSuperAdmin ? (
                <>
                  <option value="recruiter">Recruiter</option>
                  <option value="company_admin">Company Admin</option>
                  <option value="super_admin">Super Admin</option>
                </>
              ) : (
                <option value="recruiter">Recruiter</option>
              )}
            </select>
          </div>
          {isSuperAdmin && needsCompany && (
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">Company</label>
              <select value={companyId} onChange={(e) => setCompanyId(e.target.value)}
                className="input-field w-full" disabled={loading}>
                <option value="">Select a company...</option>
                {companies.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}
          <div className="flex items-center gap-3 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1" disabled={loading}>Cancel</button>
            <button type="submit" className="btn-primary flex-1 flex items-center justify-center gap-2"
              disabled={loading || !username || !fullName || !password || usernameAvailable === false || (isSuperAdmin && needsCompany && !companyId)}>
              {loading ? <><Loader2 size={16} className="animate-spin" />Creating...</> : "Create User"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Reset Password Modal ────────────────────────────────────────────

function ResetPasswordModal({ user, onClose, onReset }: { user: UserRecord; onClose: () => void; onReset: () => void }) {
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await adminApi.resetPassword(user.id, password);
      onReset();
      onClose();
    } catch (err: any) {
      setError(extractError(err, "Failed to reset password."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="glass-card-solid w-full max-w-sm p-6 animate-fade-in">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-text-primary">Reset Password</h3>
          <button onClick={onClose} className="p-1 rounded-md text-text-tertiary hover:text-text-secondary hover:bg-surface-tertiary">
            <X size={18} />
          </button>
        </div>
        <p className="text-sm text-text-secondary mb-4">
          Set a new temporary password for <strong>{user.full_name}</strong> (@{user.username}).
          They will be required to change it on next login.
        </p>

        {error && (
          <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
            <AlertCircle size={16} className="text-red-500" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="relative">
            <input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
              className="input-field w-full pr-10" placeholder="New temporary password" autoFocus disabled={loading} />
            <button type="button" onClick={() => setShowPassword(!showPassword)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary" tabIndex={-1}>
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="flex items-center gap-3">
            <button type="button" onClick={onClose} className="btn-secondary flex-1" disabled={loading}>Cancel</button>
            <button type="submit" className="btn-primary flex-1 flex items-center justify-center gap-2"
              disabled={loading || password.length < 4}>
              {loading ? <><Loader2 size={16} className="animate-spin" />Resetting...</> : "Reset Password"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// ACTIVITY LOG TAB
// ════════════════════════════════════════════════════════════════════

function ActivityLog() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [page, setPage] = useState(0);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const limit = 30;

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const params: any = { skip: page * limit, limit };
      if (search) params.search = search;
      if (actionFilter) params.action = actionFilter;
      const res = await adminApi.getAuditLogs(params);
      setLogs(res.data.logs);
      setTotal(res.data.total);
    } catch (err: any) {
      setFetchError(extractError(err, "Failed to load activity logs."));
    }
    finally { setLoading(false); }
  }, [search, actionFilter, page]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const actionColor = (action: string) => {
    if (action.includes("login")) return "bg-blue-50 text-blue-700";
    if (action.includes("create") || action.includes("upload")) return "bg-green-50 text-green-700";
    if (action.includes("delete") || action.includes("deactivate")) return "bg-red-50 text-red-700";
    if (action.includes("reset") || action.includes("change")) return "bg-amber-50 text-amber-700";
    return "bg-gray-50 text-gray-700";
  };

  const formatTime = (d: string) => {
    const date = new Date(d);
    return date.toLocaleDateString("en-US", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };

  return (
    <>
      {fetchError && (
        <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
          <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{fetchError}</p>
        </div>
      )}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-xs">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary" />
          <input type="text" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            placeholder="Search logs..." className="input-field w-full pl-9" />
        </div>
        <select value={actionFilter} onChange={(e) => { setActionFilter(e.target.value); setPage(0); }} className="input-field text-sm">
          <option value="">All Actions</option>
          <option value="login">Login</option>
          <option value="login_failed">Login Failed</option>
          <option value="logout">Logout</option>
          <option value="create_user">Create User</option>
          <option value="deactivate_user">Deactivate User</option>
          <option value="reactivate_user">Reactivate User</option>
          <option value="reset_password">Reset Password</option>
          <option value="change_password">Change Password</option>
        </select>
        <button onClick={fetchLogs} className="btn-secondary flex items-center gap-1.5">
          <RotateCcw size={14} />
          Refresh
        </button>
      </div>

      <div className="glass-card-solid overflow-hidden">
        <div className="divide-y divide-surface-border/50">
          {loading ? (
            <div className="text-center py-10 text-text-tertiary">
              <Loader2 size={20} className="animate-spin mx-auto mb-2" />Loading...
            </div>
          ) : logs.length === 0 ? (
            <div className="text-center py-10 text-text-tertiary">No activity logs found</div>
          ) : (
            logs.map((log) => (
              <div key={log.id} className="flex items-center gap-4 px-5 py-3 hover:bg-surface-secondary/50 transition-colors">
                <div className="flex-shrink-0">
                  <Clock size={14} className="text-text-tertiary" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary">@{log.username}</span>
                    <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${actionColor(log.action)}`}>
                      {log.action.replace(/_/g, " ")}
                    </span>
                  </div>
                  {log.details && (
                    <p className="text-xs text-text-tertiary mt-0.5 truncate">{log.details}</p>
                  )}
                </div>
                <div className="flex-shrink-0 text-right">
                  <p className="text-xs text-text-tertiary">{formatTime(log.created_at)}</p>
                  {log.ip_address && <p className="text-[10px] text-text-tertiary">{log.ip_address}</p>}
                </div>
              </div>
            ))
          )}
        </div>
        {total > limit && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-surface-border">
            <span className="text-xs text-text-tertiary">{total} entries total</span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}
                className="btn-secondary text-xs px-3 py-1">Previous</button>
              <span className="text-xs text-text-tertiary">Page {page + 1}</span>
              <button onClick={() => setPage(page + 1)} disabled={(page + 1) * limit >= total}
                className="btn-secondary text-xs px-3 py-1">Next</button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

// ════════════════════════════════════════════════════════════════════
// STATS TAB
// ════════════════════════════════════════════════════════════════════

function StatsPanel() {
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi.getStats().then((res) => {
      setStats(res.data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-center py-20 text-text-tertiary"><Loader2 size={24} className="animate-spin mx-auto mb-2" />Loading stats...</div>;
  }
  if (!stats) {
    return <div className="text-center py-20 text-text-tertiary">Failed to load stats</div>;
  }

  const cards = [
    { label: "Total Users", value: stats.total_users, icon: Users, color: "text-purple-500", bg: "bg-purple-50" },
    { label: "Active Users", value: stats.active_users, icon: UserCheck, color: "text-green-500", bg: "bg-green-50" },
    { label: "Logins (7 days)", value: stats.recent_logins_7d, icon: Activity, color: "text-blue-500", bg: "bg-blue-50" },
    { label: "Total Candidates", value: stats.total_candidates, icon: Users, color: "text-indigo-500", bg: "bg-indigo-50" },
    { label: "Total Jobs", value: stats.total_jobs, icon: BarChart3, color: "text-amber-500", bg: "bg-amber-50" },
    { label: "Total Analyses", value: stats.total_analyses, icon: Activity, color: "text-cyan-500", bg: "bg-cyan-50" },
    { label: "Batch Runs", value: stats.total_batch_runs, icon: BarChart3, color: "text-rose-500", bg: "bg-rose-50" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      {cards.map(({ label, value, icon: Icon, color, bg }) => (
        <div key={label} className="glass-card-solid p-5">
          <div className="flex items-center gap-3 mb-3">
            <div className={`w-9 h-9 rounded-lg ${bg} flex items-center justify-center`}>
              <Icon size={18} className={color} />
            </div>
          </div>
          <p className="text-2xl font-bold text-text-primary">{value}</p>
          <p className="text-xs text-text-tertiary mt-1 uppercase tracking-wider font-medium">{label}</p>
        </div>
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// COMPANIES TAB (super_admin only)
// ════════════════════════════════════════════════════════════════════

function CompanyManagement() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchCompanies = useCallback(async () => {
    setLoading(true);
    setActionError(null);
    try {
      const res = await adminApi.listCompanies();
      setCompanies(res.data || []);
    } catch (err: any) {
      setActionError(extractError(err, "Failed to load companies."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchCompanies(); }, [fetchCompanies]);

  const formatDate = (d: string) => {
    return new Date(d).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    });
  };

  return (
    <>
      {actionError && (
        <div className="flex items-center justify-between gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
          <div className="flex items-center gap-2">
            <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{actionError}</p>
          </div>
          <button onClick={() => setActionError(null)} className="text-red-400 hover:text-red-600 p-0.5">
            <X size={14} />
          </button>
        </div>
      )}

      <div className="flex items-center gap-3 mb-5">
        <div className="flex-1" />
        <button onClick={() => setShowCreateModal(true)} className="btn-primary flex items-center gap-1.5">
          <Plus size={15} />
          Create Company
        </button>
      </div>

      <div className="glass-card-solid overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-surface-border">
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Company</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Slug</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Users</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Status</th>
              <th className="text-left text-xs font-semibold text-text-tertiary uppercase tracking-wider px-5 py-3">Created</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="text-center py-10 text-text-tertiary">
                <Loader2 size={20} className="animate-spin mx-auto mb-2" />Loading...
              </td></tr>
            ) : companies.length === 0 ? (
              <tr><td colSpan={5} className="text-center py-10 text-text-tertiary">No companies found</td></tr>
            ) : (
              companies.map((c) => (
                <tr key={c.id} className="border-b border-surface-border/50 hover:bg-surface-secondary/50 transition-colors">
                  <td className="px-5 py-3.5">
                    <p className="text-sm font-medium text-text-primary">{c.name}</p>
                  </td>
                  <td className="px-5 py-3.5">
                    <p className="text-sm text-text-tertiary">{c.slug}</p>
                  </td>
                  <td className="px-5 py-3.5">
                    <p className="text-sm text-text-primary">{c.user_count}</p>
                  </td>
                  <td className="px-5 py-3.5">
                    {c.is_active ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700">Active</span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700">Inactive</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-text-tertiary">{formatDate(c.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showCreateModal && (
        <CreateCompanyModal onClose={() => setShowCreateModal(false)} onCreated={fetchCompanies} />
      )}
    </>
  );
}

// ── Create Company Modal ────────────────────────────────────────────

function CreateCompanyModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await adminApi.createCompany({ name, slug });
      onCreated();
      onClose();
    } catch (err: any) {
      setError(extractError(err, "Failed to create company."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="glass-card-solid w-full max-w-md p-6 animate-fade-in">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold text-text-primary">Create New Company</h3>
          <button onClick={onClose} className="p-1 rounded-md text-text-tertiary hover:text-text-secondary hover:bg-surface-tertiary">
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
            <AlertCircle size={16} className="text-red-500" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Company Name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)}
              className="input-field w-full" placeholder="Acme Corp" autoFocus disabled={loading} />
          </div>
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Slug</label>
            <input type="text" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
              className="input-field w-full" placeholder="acme-corp" disabled={loading} />
            <p className="text-xs text-text-tertiary mt-1">Lowercase alphanumeric, hyphens, underscores only</p>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary flex-1" disabled={loading}>Cancel</button>
            <button type="submit" className="btn-primary flex-1 flex items-center justify-center gap-2"
              disabled={loading || !name || !slug}>
              {loading ? <><Loader2 size={16} className="animate-spin" />Creating...</> : "Create Company"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
