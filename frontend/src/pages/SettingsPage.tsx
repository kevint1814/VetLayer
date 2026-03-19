import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { authApi } from "../services/api";
import {
  User, Lock, Shield, CheckCircle2, AlertCircle, Loader2,
  Eye, EyeOff, Calendar,
} from "lucide-react";

/** Extract a readable error from Axios errors. */
function extractError(err: any, fallback = "Something went wrong."): string {
  const detail = err?.response?.data?.detail;
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
  }
  return fallback;
}

type Tab = "profile" | "password";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("profile");

  const tabs = [
    { id: "profile" as Tab, label: "Profile", icon: User },
    { id: "password" as Tab, label: "Password", icon: Lock },
  ];

  return (
    <div className="page-container max-w-2xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-text-primary">Settings</h1>
        <p className="text-sm text-text-tertiary mt-0.5">Manage your account and preferences</p>
      </div>

      <div className="flex items-center gap-1 p-1 rounded-lg bg-surface-tertiary w-fit mb-6">
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

      {activeTab === "profile" && <ProfileSection />}
      {activeTab === "password" && <PasswordSection />}
    </div>
  );
}


function ProfileSection() {
  const { user, refreshUser } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");

  const isDirty = fullName.trim() !== (user?.full_name || "");

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isDirty) return;
    setError("");
    setSaving(true);
    setSuccess(false);
    try {
      await authApi.updateProfile({ full_name: fullName.trim() });
      await refreshUser();
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err: any) {
      setError(extractError(err, "Failed to update profile."));
    } finally {
      setSaving(false);
    }
  };

  const formatDate = (d: string | null | undefined) => {
    if (!d) return "Never";
    return new Date(d).toLocaleDateString("en-US", {
      month: "long", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };

  return (
    <div className="space-y-6">
      {/* Account info card */}
      <div className="glass-card-solid p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Account Information</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-text-tertiary text-xs uppercase tracking-wider font-medium mb-1">Username</p>
            <p className="text-text-primary font-medium">@{user?.username}</p>
          </div>
          <div>
            <p className="text-text-tertiary text-xs uppercase tracking-wider font-medium mb-1">Role</p>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
              (user?.role === "super_admin" || user?.role === "company_admin") ? "bg-purple-100 text-purple-700" : "bg-blue-50 text-blue-700"
            }`}>
              {(user?.role === "super_admin" || user?.role === "company_admin") && <Shield size={10} />}
              {user?.role === "super_admin" ? "Super Admin" : user?.role === "company_admin" ? "Company Admin" : "Recruiter"}
            </span>
          </div>
          {user?.company_name && (
            <div>
              <p className="text-text-tertiary text-xs uppercase tracking-wider font-medium mb-1">Company</p>
              <p className="text-text-primary font-medium">{user.company_name}</p>
            </div>
          )}
          <div>
            <p className="text-text-tertiary text-xs uppercase tracking-wider font-medium mb-1">Last Login</p>
            <p className="text-text-secondary text-xs flex items-center gap-1.5">
              <Calendar size={12} />
              {formatDate(user?.last_login_at)}
            </p>
          </div>
          <div>
            <p className="text-text-tertiary text-xs uppercase tracking-wider font-medium mb-1">Account Created</p>
            <p className="text-text-secondary text-xs flex items-center gap-1.5">
              <Calendar size={12} />
              {formatDate(user?.created_at)}
            </p>
          </div>
        </div>
      </div>

      {/* Edit profile form */}
      <div className="glass-card-solid p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Edit Profile</h3>

        {error && (
          <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
            <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {success && (
          <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-green-50 border border-green-200">
            <CheckCircle2 size={16} className="text-green-500 flex-shrink-0" />
            <p className="text-sm text-green-700">Profile updated successfully.</p>
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-secondary mb-1.5">Display Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="input-field w-full max-w-sm"
              placeholder="Your full name"
              disabled={saving}
            />
            <p className="text-xs text-text-tertiary mt-1">This is how your name appears across VetLayer.</p>
          </div>

          <button
            type="submit"
            disabled={!isDirty || saving}
            className="btn-primary flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? (
              <><Loader2 size={14} className="animate-spin" />Saving...</>
            ) : (
              "Save Changes"
            )}
          </button>
        </form>
      </div>
    </div>
  );
}


function PasswordSection() {
  const { changePassword } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const passwordsMatch = newPassword === confirmPassword;
  const canSubmit = currentPassword && newPassword.length >= 4 && passwordsMatch && newPassword !== currentPassword;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError("");
    setSaving(true);
    setSuccess(false);
    try {
      await changePassword(currentPassword, newPassword);
      setSuccess(true);
      // changePassword clears auth state and forces re-login
    } catch (err: any) {
      setError(extractError(err, "Failed to change password."));
      setSaving(false);
    }
  };

  return (
    <div className="glass-card-solid p-5 max-w-md">
      <h3 className="text-sm font-semibold text-text-primary mb-4">Change Password</h3>
      <p className="text-xs text-text-tertiary mb-5">
        After changing your password, you will be signed out and need to log in again.
      </p>

      {error && (
        <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200">
          <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {success && (
        <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-green-50 border border-green-200">
          <CheckCircle2 size={16} className="text-green-500 flex-shrink-0" />
          <p className="text-sm text-green-700">Password changed. Redirecting to login...</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1.5">Current Password</label>
          <div className="relative">
            <input
              type={showCurrent ? "text" : "password"}
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="input-field w-full pr-10"
              placeholder="Enter current password"
              disabled={saving}
            />
            <button
              type="button"
              onClick={() => setShowCurrent(!showCurrent)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary"
              tabIndex={-1}
            >
              {showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1.5">New Password</label>
          <div className="relative">
            <input
              type={showNew ? "text" : "password"}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="input-field w-full pr-10"
              placeholder="Enter new password"
              disabled={saving}
            />
            <button
              type="button"
              onClick={() => setShowNew(!showNew)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary"
              tabIndex={-1}
            >
              {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-text-secondary mb-1.5">Confirm New Password</label>
          <input
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className={`input-field w-full ${confirmPassword && !passwordsMatch ? "border-red-300 focus:ring-red-200" : ""}`}
            placeholder="Re-enter new password"
            disabled={saving}
          />
          {confirmPassword && !passwordsMatch && (
            <p className="text-xs text-red-500 mt-1">Passwords do not match.</p>
          )}
        </div>

        {newPassword && currentPassword && newPassword === currentPassword && (
          <p className="text-xs text-amber-600">New password must be different from current password.</p>
        )}

        <button
          type="submit"
          disabled={!canSubmit || saving}
          className="btn-primary flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? (
            <><Loader2 size={14} className="animate-spin" />Changing...</>
          ) : (
            "Change Password"
          )}
        </button>
      </form>
    </div>
  );
}
