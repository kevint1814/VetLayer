import { useState, useRef, useEffect } from "react";
import { Outlet, Link, useLocation, useNavigate } from "react-router-dom";
import { LayoutDashboard, Users, Briefcase, Zap, BarChart3, Settings, HelpCircle, ChevronRight, Shield, LogOut } from "lucide-react";
import clsx from "clsx";
import { useAuth } from "../../contexts/AuthContext";

// Route config for breadcrumbs & page titles
const routeConfig: Record<string, { title: string; parent?: string }> = {
  "/": { title: "Dashboard" },
  "/candidates": { title: "Candidates" },
  "/candidates/:id": { title: "Candidate Detail", parent: "/candidates" },
  "/jobs": { title: "Jobs" },
  "/batch": { title: "Batch Analysis" },
  "/analysis/:id": { title: "Analysis", parent: "/candidates" },
  "/ranked/:jobId": { title: "Ranked Results", parent: "/batch" },
  "/admin": { title: "Admin Panel" },
};

function getRouteInfo(pathname: string): { title: string; breadcrumbs: { label: string; path?: string }[] } {
  // Direct match
  if (routeConfig[pathname]) {
    const config = routeConfig[pathname];
    const crumbs: { label: string; path?: string }[] = [];
    if (config.parent && routeConfig[config.parent]) {
      crumbs.push({ label: routeConfig[config.parent]!.title, path: config.parent });
    }
    crumbs.push({ label: config.title });
    return { title: config.title, breadcrumbs: crumbs };
  }

  // Pattern match for dynamic routes
  if (pathname.startsWith("/candidates/")) {
    return {
      title: "Candidate Detail",
      breadcrumbs: [
        { label: "Candidates", path: "/candidates" },
        { label: "Detail" },
      ],
    };
  }
  if (pathname.startsWith("/analysis/")) {
    return {
      title: "Analysis",
      breadcrumbs: [
        { label: "Candidates", path: "/candidates" },
        { label: "Analysis" },
      ],
    };
  }
  if (pathname.startsWith("/ranked/")) {
    return {
      title: "Ranked Results",
      breadcrumbs: [
        { label: "Batch Analysis", path: "/batch" },
        { label: "Results" },
      ],
    };
  }

  return { title: "VetLayer", breadcrumbs: [{ label: "Page" }] };
}

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const routeInfo = getRouteInfo(location.pathname);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const isActive = (path: string) => {
    if (path === "/") return location.pathname === "/";
    return location.pathname.startsWith(path);
  };

  // Build nav items dynamically based on role
  const navItems = [
    { path: "/", label: "Dashboard", icon: LayoutDashboard },
    { path: "/candidates", label: "Candidates", icon: Users },
    { path: "/jobs", label: "Jobs", icon: Briefcase },
    { path: "/batch", label: "Batch Analysis", icon: Zap },
    ...(user?.role === "admin" ? [{ path: "/admin", label: "Admin Panel", icon: Shield }] : []),
  ];

  // Close menu when clicking outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleLogout = async () => {
    setShowUserMenu(false);
    await logout();
    navigate("/login");
  };

  return (
    <div className="flex h-screen overflow-hidden bg-surface-secondary">
      {/* ── Sidebar ──────────────────────────────────────────────── */}
      <aside className="w-[240px] flex-shrink-0 bg-sidebar flex flex-col border-r border-sidebar-border">
        {/* Logo */}
        <div className="px-5 pt-6 pb-5">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center">
              <BarChart3 size={16} className="text-white" />
            </div>
            <div>
              <h1 className="text-[15px] font-semibold text-white tracking-tight leading-none">
                VetLayer
              </h1>
              <p className="text-[10px] text-white/40 font-medium tracking-wide uppercase mt-0.5">
                Decision Intelligence
              </p>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 mt-1">
          <p className="px-3 pb-2 text-[10px] font-semibold text-white/25 uppercase tracking-widest">
            Menu
          </p>
          <div className="space-y-0.5">
            {navItems.map(({ path, label, icon: Icon }) => (
              <Link
                key={path}
                to={path}
                className={clsx(
                  "flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150 relative",
                  isActive(path)
                    ? "bg-sidebar-active text-white"
                    : "text-white/50 hover:text-white/80 hover:bg-sidebar-hover"
                )}
              >
                {isActive(path) && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-4 bg-white rounded-r-full" />
                )}
                <Icon size={16} strokeWidth={isActive(path) ? 2 : 1.5} />
                {label}
              </Link>
            ))}
          </div>
        </nav>

        {/* Bottom section */}
        <div className="px-3 pb-4">
          <div className="border-t border-white/[0.06] mb-3" />
          <div className="space-y-0.5">
            <div
              className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium text-white/20 cursor-default w-full"
              title="Coming soon"
            >
              <Settings size={15} strokeWidth={1.5} />
              Settings
            </div>
            <div
              className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium text-white/20 cursor-default w-full"
              title="Coming soon"
            >
              <HelpCircle size={15} strokeWidth={1.5} />
              Help
            </div>
          </div>
          <p className="px-3 pt-3 text-[10px] text-white/20 font-medium">
            v0.1.0
          </p>
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-12 flex-shrink-0 bg-white/80 backdrop-blur-sm border-b border-surface-border flex items-center justify-between px-7">
          {/* Breadcrumbs */}
          <nav className="flex items-center gap-1 text-sm">
            {routeInfo.breadcrumbs.map((crumb, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <ChevronRight size={12} className="text-text-tertiary" />}
                {crumb.path ? (
                  <Link
                    to={crumb.path}
                    className="text-text-tertiary hover:text-text-secondary transition-colors"
                  >
                    {crumb.label}
                  </Link>
                ) : (
                  <span className="text-text-primary font-medium">{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>

          {/* User menu */}
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="flex items-center gap-2.5 px-2 py-1 rounded-lg hover:bg-surface-tertiary transition-colors"
            >
              <div className="text-right hidden sm:block">
                <p className="text-xs font-medium text-text-primary leading-none">{user?.full_name}</p>
                <p className="text-[10px] text-text-tertiary mt-0.5">
                  {user?.role === "admin" ? "Administrator" : "Recruiter"}
                </p>
              </div>
              <div className={`w-7 h-7 rounded-full flex items-center justify-center ${
                user?.role === "admin" ? "bg-purple-100" : "bg-brand-50"
              }`}>
                <span className={`text-2xs font-semibold ${
                  user?.role === "admin" ? "text-purple-600" : "text-brand-500"
                }`}>
                  {user?.full_name?.charAt(0).toUpperCase() || "U"}
                </span>
              </div>
            </button>

            {/* Dropdown */}
            {showUserMenu && (
              <div className="absolute right-0 top-full mt-1.5 w-52 glass-card-solid shadow-lg py-1.5 z-50 animate-fade-in">
                <div className="px-3 py-2 border-b border-surface-border mb-1">
                  <p className="text-sm font-medium text-text-primary">{user?.full_name}</p>
                  <p className="text-xs text-text-tertiary">@{user?.username}</p>
                  <span className={`inline-flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                    user?.role === "admin" ? "bg-purple-100 text-purple-700" : "bg-blue-50 text-blue-700"
                  }`}>
                    {user?.role === "admin" && <Shield size={9} />}
                    {user?.role === "admin" ? "Admin" : "Recruiter"}
                  </span>
                </div>
                <button
                  onClick={handleLogout}
                  className="flex items-center gap-2 w-full px-3 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
                >
                  <LogOut size={14} />
                  Sign out
                </button>
              </div>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
