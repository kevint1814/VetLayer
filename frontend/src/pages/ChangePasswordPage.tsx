import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { Eye, EyeOff, AlertCircle, CheckCircle2, Loader2, Shield } from "lucide-react";

export default function ChangePasswordPage() {
  const { changePassword, user } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Password strength checks
  const hasLength = newPassword.length >= 4;
  const passwordsMatch = newPassword === confirmPassword && confirmPassword.length > 0;
  const allValid = hasLength && passwordsMatch;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!allValid) return;
    setError("");
    setLoading(true);
    try {
      await changePassword(currentPassword, newPassword);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (!detail) setError("Failed to change password.");
      else if (typeof detail === "string") setError(detail);
      else if (Array.isArray(detail)) setError(detail.map((d: any) => d.msg || JSON.stringify(d)).join("; "));
      else setError("Failed to change password.");
    } finally {
      setLoading(false);
    }
  };

  const Check = ({ ok, label }: { ok: boolean; label: string }) => (
    <div className="flex items-center gap-2">
      {ok ? (
        <CheckCircle2 size={14} className="text-green-500" />
      ) : (
        <div className="w-3.5 h-3.5 rounded-full border border-gray-300" />
      )}
      <span className={`text-xs ${ok ? "text-green-700" : "text-text-tertiary"}`}>{label}</span>
    </div>
  );

  return (
    <div className="min-h-screen bg-surface-secondary flex items-center justify-center p-4">
      <div className="w-full max-w-[420px]">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-amber-100 mb-4">
            <Shield size={22} className="text-amber-600" />
          </div>
          <h1 className="text-xl font-bold text-text-primary">Set Your Password</h1>
          <p className="text-sm text-text-tertiary mt-1">
            Welcome, {user?.full_name}. Please set a new password to continue.
          </p>
        </div>

        {/* Form card */}
        <div className="glass-card-solid p-7">
          {error && (
            <div className="flex items-center gap-2 p-3 mb-5 rounded-lg bg-red-50 border border-red-200">
              <AlertCircle size={16} className="text-red-500 flex-shrink-0" />
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">
                Temporary Password
              </label>
              <div className="relative">
                <input
                  type={showCurrent ? "text" : "password"}
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="input-field w-full pr-10"
                  placeholder="Enter the password given by your admin"
                  autoComplete="current-password"
                  autoFocus
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowCurrent(!showCurrent)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary transition-colors"
                  tabIndex={-1}
                >
                  {showCurrent ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">
                New Password
              </label>
              <div className="relative">
                <input
                  type={showNew ? "text" : "password"}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="input-field w-full pr-10"
                  placeholder="Choose a strong password"
                  autoComplete="new-password"
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowNew(!showNew)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-secondary transition-colors"
                  tabIndex={-1}
                >
                  {showNew ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-text-secondary mb-1.5">
                Confirm New Password
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="input-field w-full"
                placeholder="Re-enter your new password"
                autoComplete="new-password"
                disabled={loading}
              />
            </div>

            {/* Password requirements */}
            {newPassword.length > 0 && (
              <div className="p-3 rounded-lg bg-surface-tertiary space-y-1.5">
                <Check ok={hasLength} label="At least 4 characters" />
                {confirmPassword.length > 0 && (
                  <Check ok={passwordsMatch} label="Passwords match" />
                )}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !allValid || !currentPassword}
              className="btn-primary w-full flex items-center justify-center gap-2 mt-2"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Updating...
                </>
              ) : (
                "Set Password & Continue"
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
