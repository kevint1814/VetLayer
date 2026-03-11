import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { authApi, setAccessToken } from "../services/api";

export interface AuthUser {
  id: string;
  username: string;
  full_name: string;
  role: "admin" | "recruiter";
  is_active: boolean;
  force_password_change: boolean;
  last_login_at: string | null;
  created_at: string;
}

interface AuthState {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  forcePasswordChange: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [forcePasswordChange, setForcePasswordChange] = useState(false);

  // Try to restore session on mount via refresh token cookie
  useEffect(() => {
    const tryRefresh = async () => {
      try {
        const res = await authApi.refresh();
        setAccessToken(res.data.access_token);
        setUser(res.data.user);
        setForcePasswordChange(res.data.force_password_change);
      } catch {
        // No valid session — stay on login
        setAccessToken(null);
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    };
    tryRefresh();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await authApi.login(username, password);
    setAccessToken(res.data.access_token);
    setUser(res.data.user);
    setForcePasswordChange(res.data.force_password_change);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Even if logout API fails, clear local state
    }
    setAccessToken(null);
    setUser(null);
    setForcePasswordChange(false);
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await authApi.changePassword(currentPassword, newPassword);
    // Server invalidates the session (clears refresh cookie) on password change.
    // Clear local state and force re-login with new credentials.
    setAccessToken(null);
    setUser(null);
    setForcePasswordChange(false);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const res = await authApi.me();
      setUser(res.data);
    } catch {
      // Ignore
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        forcePasswordChange,
        login,
        logout,
        changePassword,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
