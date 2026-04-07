'use client';

import { useState, type MouseEvent } from 'react';

const ICON_PROPS = {
  width: 16,
  height: 16,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'square' as const,
  strokeLinejoin: 'miter' as const,
};

export function CopyButton() {
  const [copied, setCopied] = useState(false);

  const onClick = (event: MouseEvent<HTMLButtonElement>) => {
    const pre = event.currentTarget.closest('pre');
    const text = pre?.querySelector('code')?.textContent?.replace(/\n$/, '') ?? '';
    if (!text) return;
    void navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <button
      type="button"
      className={`d-copy-code${copied ? ' is-copied' : ''}`}
      aria-label={copied ? 'Copied' : 'Copy code block'}
      onClick={onClick}
    >
      {copied ? (
        <svg {...ICON_PROPS}>
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        <svg {...ICON_PROPS}>
          <rect x="8" y="8" width="13" height="13" />
          <polyline points="5 16 3 16 3 3 16 3 16 5" />
        </svg>
      )}
    </button>
  );
}
