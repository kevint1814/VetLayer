import { Routes, Route, Link, Navigate } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";
import Layout from "./components/common/Layout";
import LoginPage from "./pages/LoginPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import DashboardPage from "./pages/DashboardPage";
import CandidatesPage from "./pages/CandidatesPage";
import CandidateDetailPage from "./pages/CandidateDetailPage";
import JobsPage from "./pages/JobsPage";
import AnalysisPage from "./pages/AnalysisPage";
import BatchAnalysisPage from "./pages/BatchAnalysisPage";
import RankedResultsPage from "./pages/RankedResultsPage";
import AdminPage from "./pages/AdminPage";
import { Loader2 } from "lucide-react";

function NotFoundPage() {
  return (
    <div className="p-10 text-center">
      <h1 className="text-4xl font-bold text-text-primary mb-2">404</h1>
      <p className="text-text-secondary mb-6">Page not found</p>
      <Link to="/" className="btn-primary inline-flex">
        Back to Dashboard
      </Link>
    </div>
  );
}

function LoadingScreen() {
  return (
    <div className="min-h-screen bg-surface-secondary flex items-center justify-center">
      <div className="text-center">
        <Loader2 size={28} className="animate-spin mx-auto mb-3 text-brand-500" />
        <p className="text-sm text-text-tertiary">Loading VetLayer...</p>
      </div>
    </div>
  );
}

export default function App() {
  const { isAuthenticated, isLoading, forcePasswordChange, user } = useAuth();

  // Show loading while checking auth state
  if (isLoading) return <LoadingScreen />;

  // Not authenticated -> login page
  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  // Authenticated but must change password
  if (forcePasswordChange) {
    return (
      <Routes>
        <Route path="*" element={<ChangePasswordPage />} />
      </Routes>
    );
  }

  // Fully authenticated
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/candidates/:id" element={<CandidateDetailPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/analysis/:id" element={<AnalysisPage />} />
        <Route path="/batch" element={<BatchAnalysisPage />} />
        <Route path="/ranked/:jobId" element={<RankedResultsPage />} />
        {user?.role === "admin" && (
          <Route path="/admin" element={<AdminPage />} />
        )}
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
