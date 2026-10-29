// Build-time rollback for the A capability bar. The single-user local build
// defaults to on; `VITE_CAPABILITY_BAR_A=off` restores the old Composer without
// writing selections or sending capability_ids.
export const CAPABILITY_BAR_A =
  (import.meta.env.VITE_CAPABILITY_BAR_A ?? 'on') !== 'off';

// The manager and the Composer bar are one capability surface. Keep the
// historical env name so one build switch rolls both back together.
export const CAPABILITY_MANAGER_V2 = CAPABILITY_BAR_A;
