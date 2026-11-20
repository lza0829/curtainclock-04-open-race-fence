async function req(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    // 开窗 409 的 detail 是结构化回执：{ error, receipt_seq, peer_seq, ... }
    const detail = data?.detail;
    const msg =
      (detail && typeof detail === "object" ? detail.error : detail) ||
      data?.error ||
      res.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    err.receipt = detail && typeof detail === "object" ? detail : null;
    throw err;
  }
  return data;
}

export const api = {
  dashboard: () => req("/dashboard"),
  rooms: () => req("/rooms"),
  teachers: () => req("/teachers"),
  occupancy: () => req("/occupancy"),
  sessions: () => req("/sessions"),
  leases: () => req("/leases"),
  conflicts: () => req("/conflicts"),
  receipts: () => req("/receipts"),
  openLease: (session_code) =>
    req("/leases/open", { method: "POST", body: JSON.stringify({ session_code }) }),
  closeLease: (lease_no) =>
    req("/leases/close", { method: "POST", body: JSON.stringify({ lease_no }) }),
};
