"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import type { Root, Node } from "fumadocs-core/page-tree";

export function DocsSidebar({ tree }: { tree: Root }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="d-menu-toggle"
        onClick={() => setOpen(!open)}
        aria-label="Toggle navigation"
      >
        {open ? "\u2715" : "\u2630"}
      </button>
      {open && (
        <div className="d-overlay" onClick={() => setOpen(false)} />
      )}
      <aside className={`d-sidebar${open ? " is-open" : ""}`}>
        <div className="d-sidebar-header">
          <a href="/" className="d-sidebar-brand">
            kensa
          </a>
          <span className="d-sidebar-label">docs</span>
        </div>
        <nav className="d-sidebar-nav">
          {tree.children.map((node: Node, i: number) => {
            if (node.type === "separator") {
              return <div key={i} className="d-sidebar-sep" />;
            }
            if (node.type === "page") {
              const active = pathname === node.url;
              return (
                <a
                  key={node.url}
                  href={node.url}
                  className={`d-sidebar-link${active ? " d-sidebar-link--active" : ""}`}
                  onClick={() => setOpen(false)}
                >
                  {node.name}
                </a>
              );
            }
            return null;
          })}
        </nav>
        <div className="d-sidebar-footer">
          <a
            href="https://github.com/satyaborg/kensa"
            className="d-sidebar-link"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
        </div>
      </aside>
    </>
  );
}
