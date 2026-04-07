'use client';

import { useEffect, useState, type ReactNode } from 'react';

interface TocItem {
  title: ReactNode;
  url: string;
  depth: number;
}

export function DocsToc({ items }: { items: TocItem[] }) {
  const [activeId, setActiveId] = useState<string>('');

  useEffect(() => {
    if (items.length === 0) return;

    const ids = items.map((item) => item.url.replace('#', ''));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
          }
        }
      },
      { rootMargin: '0px 0px -80% 0px', threshold: 0 },
    );

    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }

    return () => observer.disconnect();
  }, [items]);

  if (items.length === 0) return null;

  return (
    <aside className="d-toc">
      <p className="d-toc-label">On this page</p>
      <ul className="d-toc-list">
        {items.map((item) => (
          <li key={item.url}>
            <a
              href={item.url}
              className={`d-toc-link${item.depth > 2 ? ' d-toc-link--nested' : ''}${
                activeId === item.url.replace('#', '') ? ' d-toc-link--active' : ''
              }`}
            >
              {item.title}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
