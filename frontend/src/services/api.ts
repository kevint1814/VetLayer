/**
 * VetLayer API client — all backend communication goes through here.
 */

import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
  withCredentials: true, // Send HttpOnly cookies with requests
});

// ── Token management ────────────────────────────────────────────────

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

// ── Request interceptor: attach Bearer token ────────────────────────

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

// ── Response interceptor: handle 401 with token refresh ─────────────

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}> = [];

function processQueue(error: unknown, token: string | null) {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token!);
    }
  });
  failedQueue = [];
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Skip auth endpoints to avoid infinite loops
    if (
      originalRequest?.url?.startsWith("/auth/") ||
      error.response?.status !== 401 ||
      originalRequest?._retry
    ) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({
          resolve: (token: string) => {
            originalRequest.headers.Authorization = `Bearer ${token}`;
            resolve(api(originalRequest));
          },
          reject,
        });
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      const res = await api.post("/auth/refresh");
      const newToken = res.data.access_token;
      setAccessToken(newToken);
      processQueue(null, newToken);
      originalRequest.headers.Authorization = `Bearer ${newToken}`;
      return api(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError, null);
      setAccessToken(null);
      // Redirect to login
      window.location.href = "/login";
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  }
);

// ── Auth ────────────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    api.post("/auth/login", { username, password }),

  refresh: () => api.post("/auth/refresh"),

  logout: () => api.post("/auth/logout"),

  changePassword: (currentPassword: string, newPassword: string) =>
    api.post("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),

  me: () => api.get("/auth/me"),
};

// ── Admin ───────────────────────────────────────────────────────────

export const adminApi = {
  listUsers: (params?: { role?: string; status?: string; search?: string; skip?: number; limit?: number }) =>
    api.get("/admin/users", { params }),

  createUser: (data: { username: string; full_name: string; password: string; role?: string }) =>
    api.post("/admin/users", data),

  checkUsername: (username: string) =>
    api.get("/admin/users/check-username", { params: { username } }),

  deactivateUser: (userId: string) =>
    api.post(`/admin/users/${userId}/deactivate`),

  reactivateUser: (userId: string) =>
    api.post(`/admin/users/${userId}/reactivate`),

  resetPassword: (userId: string, newPassword: string) =>
    api.post(`/admin/users/${userId}/reset-password`, { new_password: newPassword }),

  getAuditLogs: (params?: { username?: string; action?: string; target_type?: string; search?: string; skip?: number; limit?: number }) =>
    api.get("/admin/audit-logs", { params }),

  getStats: () => api.get("/admin/stats"),
};

// ── Candidates ──────────────────────────────────────────────────────

export const candidatesApi = {
  list: (params?: { skip?: number; limit?: number; search?: string }) =>
    api.get("/candidates/", { params }),

  get: (id: string) => api.get(`/candidates/${id}`),

  create: (data: { name: string; resume_filename: string }) =>
    api.post("/candidates/", data),

  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post("/candidates/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },

  bulkUpload: (files: File[]) => {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    return api.post("/candidates/bulk-upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000, // 5 min for multiple files
    });
  },

  delete: (id: string) => api.delete(`/candidates/${id}`),

  bulkDelete: (ids: string[]) =>
    api.post("/candidates/delete", { ids }),

  exportIntelligenceBrief: (id: string) =>
    api.get(`/candidates/${id}/export/intelligence-brief`, { responseType: "blob" }),
};

// ── Jobs ────────────────────────────────────────────────────────────

export const jobsApi = {
  list: (params?: { skip?: number; limit?: number; search?: string }) =>
    api.get("/jobs/", { params }),

  get: (id: string) => api.get(`/jobs/${id}`),

  create: (data: { title: string; description: string; company?: string; department?: string; required_skills?: any[]; preferred_skills?: any[]; experience_range?: any; education_requirements?: any; location?: string; remote_policy?: string }) =>
    api.post("/jobs/", data),

  createSmart: (data: {
    title: string;
    company?: string;
    location?: string;
    remote_policy?: string;
    description?: string;
    raw_requirements: string;
  }) => api.post("/jobs/smart", data, { timeout: 60000 }),

  update: (id: string, data: Partial<{
    title: string; company: string; department: string; description: string;
    required_skills: any[]; preferred_skills: any[]; experience_range: any;
    education_requirements: any; location: string; remote_policy: string;
  }>) => api.put(`/jobs/${id}`, data),

  reparse: (id: string, raw_requirements: string) =>
    api.post(`/jobs/${id}/reparse`, { raw_requirements }, { timeout: 60000 }),

  delete: (id: string) => api.delete(`/jobs/${id}`),

  bulkDelete: (ids: string[]) =>
    api.post("/jobs/delete", { ids }),
};

// ── Analysis ────────────────────────────────────────────────────────

export const analysisApi = {
  trigger: (candidateId: string, jobId: string) =>
    api.post("/analysis/run", {
      candidate_id: candidateId,
      job_id: jobId,
    }, { timeout: 300000 }), // 5 min — pipeline makes 3 sequential LLM calls

  get: (id: string) => api.get(`/analysis/${id}`),

  forCandidate: (candidateId: string) =>
    api.get(`/analysis/candidate/${candidateId}`),

  delete: (id: string) => api.delete(`/analysis/${id}`),

  bulkDelete: (ids: string[]) =>
    api.post("/analysis/delete", { ids }),

  // Batch analysis
  triggerBatch: (candidateIds: string[], jobIds: string[], forceReanalyze = false) =>
    api.post("/analysis/batch", {
      candidate_ids: candidateIds,
      job_ids: jobIds,
      force_reanalyze: forceReanalyze,
    }, { timeout: 600000 }), // 10 min for large batches

  getBatchProgress: (batchId: string) =>
    api.get(`/analysis/batch/${batchId}`),

  listBatches: () =>
    api.get("/analysis/batch"),

  deleteBatch: (batchId: string) =>
    api.delete(`/analysis/batch/${batchId}`),

  exportBatchBrief: (batchId: string, jobId: string) =>
    api.get(`/analysis/batch/${batchId}/export/brief?job_id=${jobId}`, { responseType: "blob" }),

  // Ranked results
  getRanked: (jobId: string) =>
    api.get(`/analysis/ranked/${jobId}`),
};

// ── Health ──────────────────────────────────────────────────────────

export const healthApi = {
  check: () => api.get("/health"),
};

export default api;
