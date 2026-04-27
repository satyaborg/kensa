"use client";

import { useCallback, useState } from "react";

const ICON_PROPS = {
  width: 14,
  height: 14,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "square" as const,
  strokeLinejoin: "miter" as const,
};

const INSTALL_CMD = "uvx kensa init";
const INSTALL_NOTE =
  "Adds kensa CLI, scaffolds your project, and drops skills into whichever coding agent you use. Python 3.10+.";

function CopyIcon({ copied }: { copied: boolean }) {
  return copied ? (
    <svg {...ICON_PROPS}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  ) : (
    <svg {...ICON_PROPS}>
      <rect x="8" y="8" width="13" height="13" />
      <polyline points="5 16 3 16 3 3 16 3 16 5" />
    </svg>
  );
}

export function InstallTabs() {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(INSTALL_CMD);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, []);

  return (
    <div className="a-install-tabs-wrapper">
      <div
        className="a-install-panel"
        role="button"
        tabIndex={0}
        onClick={handleCopy}
      >
        <div className="a-install-lines">
          <div className="a-install-row">
            <span className="a-prompt">$</span>
            <code>{INSTALL_CMD}</code>
          </div>
        </div>
        <span
          className={`a-copy${copied ? " a-copy-copied" : ""}`}
          aria-label="Copy"
        >
          <CopyIcon copied={copied} />
        </span>
      </div>
      <p className="a-install-note">{INSTALL_NOTE}</p>
    </div>
  );
}
