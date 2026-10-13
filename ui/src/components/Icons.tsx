const base = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.3 } as const;

export const IconShield = () => (
  <svg {...base}><path d="M8 1.5l5.5 2v4c0 3-2.3 5.4-5.5 7-3.2-1.6-5.5-4-5.5-7v-4z" /></svg>
);
export const IconCheck = () => (
  <svg {...base}><circle cx="8" cy="8" r="6.2" /><path d="M5.2 8.2l2 2 3.6-4" /></svg>
);
export const IconCog = () => (
  <svg {...base}>
    <circle cx="8" cy="8" r="2.2" />
    <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" />
  </svg>
);
export const IconHome = () => (
  <svg {...base}><path d="M2 7.5L8 2.5l6 5M3.5 6.5v7h9v-7" /></svg>
);
export const IconArrow = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M3 9l6-6M4 3h5v5" /></svg>
);
export const IconChevron = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.3"><path d="M2 3.5l3 3 3-3" /></svg>
);
export const IconSend = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor"><path d="M2 2l14 7-14 7 2.5-7z" /></svg>
);
export const IconSearch = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"><circle cx="7" cy="7" r="5" /><path d="M11 11l4 4" /></svg>
);

export const BrandMark = () => (
  <svg width="44" height="44" viewBox="0 0 44 44" fill="none" aria-hidden="true">
    <circle cx="22" cy="22" r="20" fill="#e0ecd6" stroke="#3f6b32" strokeWidth="1.2" />
    <ellipse cx="22" cy="22" rx="14" ry="6" stroke="#3f6b32" strokeWidth="1" transform="rotate(-28 22 22)" />
    <circle cx="22" cy="22" r="4.2" fill="#121c16" />
    <circle cx="33" cy="15.5" r="2.4" fill="#3f6b32" />
  </svg>
);
