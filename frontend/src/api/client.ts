/**
 * src/api/client.ts
 *
 * Axios instance pre-configured for the ParkGuard event-api.
 *
 * Every request automatically receives the `X-Operator-Name` header
 * from the Zustand store (honour-system — no auth, spec §11 / §17).
 * The base URL is relative so the Vite proxy rewrites `/api/*` to
 * `http://localhost:8000/api/*` in development; in production nginx
 * does the same.
 */

import axios from "axios";
import { getOperatorHeader } from "@/stores/operatorStore";

export const apiClient = axios.create({
  baseURL: "/api/v1",
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

// Inject operator name before every request.
apiClient.interceptors.request.use((config) => {
  const name = getOperatorHeader();
  if (name) {
    config.headers["X-Operator-Name"] = name;
  }
  return config;
});

// ------------------------------------------------------------------
// Typed helpers
// ------------------------------------------------------------------

export interface Camera {
  id: string;
  name: string;
  rtsp_substream_url: string;
  rtsp_mainstream_url?: string | null;
  location_description?: string | null;
  enabled: boolean;
  config?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Zone {
  id: number;
  camera_id: string;
  name: string;
  polygon_wkt: string;
  zone_type: string;
  threshold_seconds: number;
  exit_confirm_seconds: number;
  cooldown_seconds: number;
  color_hex: string;
  enabled: boolean;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export type ViolationStatus = "pending" | "approved" | "disputed" | "card_issued";

export interface Violation {
  id: number;
  camera_id: string;
  zone_id: number;
  cvi_id?: string | null;
  plate_text?: string | null;
  plate_format?: string | null;
  plate_region_code?: string | null;
  plate_confidence?: number | null;
  vehicle_class?: string | null;
  first_seen_in_zone: string;
  violation_time: string;
  exit_confirmed_time?: string | null;
  duration_seconds?: number | null;
  snapshot_vehicle_url?: string | null;
  snapshot_plate_url?: string | null;
  status: ViolationStatus;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  penalty_card_url?: string | null;
  created_at: string;
}

// ---- Cameras ----
export const cameras = {
  list: (params?: { enabled?: boolean }) =>
    apiClient.get<Camera[]>("/cameras", { params }).then((r) => r.data),

  get: (id: string) =>
    apiClient.get<Camera>(`/cameras/${id}`).then((r) => r.data),
};

// ---- Violations ----
export interface ViolationFilters {
  camera_id?: string;
  zone_id?: number;
  status?: ViolationStatus;
  from_time?: string;
  to_time?: string;
  limit?: number;
  offset?: number;
}

export const violations = {
  list: (filters?: ViolationFilters) =>
    apiClient.get<Violation[]>("/violations", { params: filters }).then((r) => r.data),

  get: (id: number) =>
    apiClient.get<Violation>(`/violations/${id}`).then((r) => r.data),

  patch: (id: number, body: { status: ViolationStatus }) =>
    apiClient.patch<Violation>(`/violations/${id}`, body).then((r) => r.data),

  triggerPenaltyCard: (id: number) =>
    apiClient
      .post<{ status: string; violation_id: number; url?: string }>(
        `/violations/${id}/penalty-card`
      )
      .then((r) => r.data),
};

// ---- Health ----
export const health = {
  liveness: () =>
    apiClient.get<{ status: string }>("/../../healthz").then((r) => r.data),
};
