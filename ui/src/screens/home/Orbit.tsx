const label = { fontSize: 10, textAnchor: 'middle', fontFamily: 'Inter Tight, Arial', fontWeight: 700 } as const;

/** Abstract orbit illustration: IAOS's own visual language (no mascots). */
export function Orbit() {
  return (
    <svg className="orbit" viewBox="0 0 330 300" fill="none" aria-hidden="true">
      <defs>
        <radialGradient id="rg" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#dfe9d3" />
          <stop offset="1" stopColor="#eef1ea" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="165" cy="150" r="140" fill="url(#rg)" />
      <circle cx="165" cy="150" r="104" stroke="#c2ccb8" strokeWidth="1" />
      <circle cx="165" cy="150" r="62" stroke="#c2ccb8" strokeWidth="1" strokeDasharray="3 5" />
      <ellipse cx="165" cy="150" rx="132" ry="52" stroke="#3f6b32" strokeWidth="1.1" transform="rotate(-24 165 150)" />
      <circle cx="165" cy="150" r="17" fill="#121c16" />
      <circle cx="165" cy="150" r="30" stroke="#121c16" strokeWidth="1" />
      <circle cx="76" cy="107" r="9" fill="#f6f8f2" stroke="#121c16" strokeWidth="1.3" />
      <text x="76" y="111" {...label}>A</text>
      <circle cx="262" cy="86" r="9" fill="#3f6b32" />
      <text x="262" y="90" fill="#fff" {...label}>E</text>
      <circle cx="238" cy="222" r="9" fill="#f6f8f2" stroke="#3f6b32" strokeWidth="1.3" />
      <text x="238" y="226" {...label}>V</text>
      <circle cx="110" cy="232" r="9" fill="#e0ecd6" stroke="#3f6b32" strokeWidth="1.3" />
      <text x="110" y="236" {...label}>I</text>
      <path d="M85 112 L150 140M171 136 L255 92M168 165 L232 216M158 164 L116 224" stroke="#93a198" strokeWidth=".8" strokeDasharray="2 4" />
    </svg>
  );
}
