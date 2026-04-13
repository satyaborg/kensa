"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";

function Logo() {
  return (
    <Link href="/" className="a-nav-logo">
      kensa
    </Link>
  );
}

export function LandingPageNav() {
  const navRef = useRef<HTMLElement>(null);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const navElement = navRef.current;
    if (!navElement) {
      return;
    }

    const updateProgress = () => {
      frameRef.current = null;
      navElement.style.setProperty(
        "--nav-progress",
        String(Math.min(window.scrollY / 96, 1)),
      );
    };

    const onScroll = () => {
      if (frameRef.current !== null) {
        return;
      }

      frameRef.current = window.requestAnimationFrame(updateProgress);
    };

    updateProgress();
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, []);

  return (
    <nav ref={navRef} className="a-nav">
      <div className="a-nav-inner">
        <Logo />
        <div className="a-nav-links">
          <Link href="/docs">Docs</Link>
          <Link href="/docs/changelog">Changelog</Link>
          <a
            href="https://github.com/satyaborg/kensa"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
        </div>
      </div>
    </nav>
  );
}
