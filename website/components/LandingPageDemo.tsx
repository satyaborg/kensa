"use client";

import { useEffect, useRef, useState, type FocusEvent } from "react";

export function LandingPageDemo() {
  const [hovered, setHovered] = useState(false);
  const [paused, setPaused] = useState(false);
  const [hasFocusWithin, setHasFocusWithin] = useState(false);
  const [showControlsForTouch, setShowControlsForTouch] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(hover: none), (pointer: coarse)");
    const updateControls = () => setShowControlsForTouch(mediaQuery.matches);

    updateControls();

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener("change", updateControls);
      return () => mediaQuery.removeEventListener("change", updateControls);
    }

    mediaQuery.addListener(updateControls);
    return () => mediaQuery.removeListener(updateControls);
  }, []);

  const handleBlurCapture = (
    event: FocusEvent<HTMLDivElement>,
  ) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setHasFocusWithin(false);
    }
  };

  return (
    <div
      className="a-demo-wrap"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setHasFocusWithin(true)}
      onBlurCapture={handleBlurCapture}
    >
      <video
        ref={videoRef}
        className="a-demo-video"
        src="/demo.mp4"
        poster="/thumbnail.png"
        controls={showControlsForTouch || hovered || hasFocusWithin}
        preload="metadata"
        autoPlay
        muted
        loop
        playsInline
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
      />
      {paused && (
        <button
          type="button"
          className="a-demo-play-btn"
          onClick={() => videoRef.current?.play()}
          aria-label="Play demo"
        >
          <svg width="44" height="44" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
      )}
    </div>
  );
}
