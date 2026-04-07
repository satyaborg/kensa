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

interface InstallMethod {
  key: string;
  label: string;
  prompt?: string;
  lines: { comment?: string; cmd: string }[];
  note: string;
}

const methods: InstallMethod[] = [
  {
    key: "skill",
    label: "Skill",
    lines: [{ cmd: "npx skills add satyaborg/kensa" }],
    note: "Installs eval skills for coding agents. CLI auto-installs on first use.",
  },
  {
    key: "cli",
    label: "CLI",
    lines: [{ cmd: "uv add kensa" }],
    note: "Adds kensa as a project dependency (Python 3.10+)",
  },
  {
    key: "plugin",
    label: "Plugin",
    prompt: "",
    lines: [
      { cmd: "/plugin marketplace add satyaborg/kensa" },
      { cmd: "/plugin install kensa" },
    ],
    note: "Claude Code only. Same skills as npx, updated through the marketplace.",
  },
];

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
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);

  const method = methods[active];
  const copyText = method.lines.map((l) => l.cmd).join("\n");

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(copyText);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [copyText]);

  return (
    <div className="a-install-tabs-wrapper">
      <div className="a-install-tablist" role="tablist">
        {methods.map((m, i) => (
          <button
            key={m.key}
            role="tab"
            className={`a-install-tab${i === active ? " a-install-tab--active" : ""}`}
            aria-selected={i === active}
            onClick={() => {
              setActive(i);
              setCopied(false);
            }}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div
        className="a-install-panel"
        role="button"
        tabIndex={0}
        onClick={handleCopy}
      >
        <div className="a-install-lines">
          {method.lines.map((line, i) => (
            <div key={i}>
              {line.comment && (
                <div className="a-install-comment">{line.comment}</div>
              )}
              <div className="a-install-row">
                {(method.prompt ?? "$") && (
                  <span className="a-prompt">{method.prompt ?? "$"}</span>
                )}
                <code>{line.cmd}</code>
              </div>
            </div>
          ))}
        </div>
        <span
          className={`a-copy${copied ? " a-copy-copied" : ""}`}
          aria-label="Copy"
        >
          <CopyIcon copied={copied} />
        </span>
      </div>
      <p className="a-install-note">{method.note}</p>
    </div>
  );
}
