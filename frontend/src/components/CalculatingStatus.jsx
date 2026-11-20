import { useEffect, useRef } from 'react';
import './calculatingStatus.css';

export default function CalculatingStatus() {
  const videoRef = useRef(null);
  useEffect(() => {
    const video = videoRef.current;
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const pause = () => {
      if (video && !video.paused) video.pause();
    };
    const update = () => {
      if (document.hidden || media?.matches || document.documentElement.dataset.theme === 'light')
        pause();
      else
        video?.play()?.catch(() => {
          /* The poster remains visible if autoplay is unavailable. */
        });
    };
    const themeObserver = new MutationObserver(update);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    update();
    document.addEventListener('visibilitychange', update);
    media?.addEventListener?.('change', update);
    return () => {
      themeObserver.disconnect();
      document.removeEventListener('visibilitychange', update);
      media?.removeEventListener?.('change', update);
      pause();
    };
  }, []);
  return (
    <div
      className="ask-calculating-status"
      id="query-processing-status"
      role="status"
      aria-label="Calculating answer"
      aria-live="polite"
    >
      <span className="calculating-light-orbit" aria-hidden="true">
        <span className="calculating-orbit-track" />
        <span className="calculating-orbit-core" />
        <span className="calculating-orbit-satellite" />
      </span>
      <span className="calculating-burst" aria-hidden="true">
        <img className="calculating-burst-poster" src="/media/calculating-burst.webp" alt="" />
        <video
          ref={videoRef}
          className="calculating-burst-video"
          src="/media/calculating-burst.mp4"
          muted
          loop
          playsInline
          preload="auto"
          disableRemotePlayback
          tabIndex={-1}
          aria-hidden="true"
        />
      </span>
      <span className="calculating-label" aria-hidden="true">
        Calculating<span className="calculating-ellipsis">…</span>
      </span>
      <span className="calculating-sr-only">Calculating your answer. Please wait.</span>
    </div>
  );
}
