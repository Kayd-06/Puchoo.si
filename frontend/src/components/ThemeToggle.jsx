import { useEffect, useRef } from 'react';
import './themeToggle.css';
import { useTheme } from '../context/ThemeContext';

function pixelShape(kind) {
  const pixels = [];
  for (let y = 1; y < 23; y += 1) {
    for (let x = 1; x < 23; x += 1) {
      const dx = x - 11.5;
      const dy = y - 11.5;
      const radius = Math.hypot(dx, dy);
      const sun =
        radius < 4.4 ||
        (radius > 6.2 &&
          radius < 9.3 &&
          (Math.abs(dx) < 0.65 ||
            Math.abs(dy) < 0.65 ||
            (Math.abs(Math.abs(dx) - Math.abs(dy)) < 0.65 && radius < 8.5)));
      const moon = Math.hypot(x - 10.8, y - 12.8) < 7.1 && Math.hypot(x - 14, y - 9.8) > 6.4;
      const star =
        (x === 17 && y >= 3 && y <= 5) ||
        (y === 4 && x >= 16 && x <= 18) ||
        (x === 20 && y === 9) ||
        (x === 13 && y === 2);
      if (kind === 'light' ? sun : moon || star) pixels.push({ x, y });
    }
  }
  return pixels;
}
const SHAPES = { dark: pixelShape('dark'), light: pixelShape('light') };
const PIXEL_COUNT = Math.max(SHAPES.dark.length, SHAPES.light.length);
const point = (theme, index) => SHAPES[theme][index] || { x: 11.5, y: 11.5 };

export default function ThemeToggle({ className = '' }) {
  const { theme, toggleTheme } = useTheme();
  const previousTheme = useRef(theme);
  const pixelsRef = useRef(null);
  const rimRef = useRef(null);
  const isDark = theme === 'dark';
  const nextTheme = isDark ? 'light' : 'dark';
  useEffect(() => {
    const fromTheme = previousTheme.current;
    previousTheme.current = theme;
    if (fromTheme === theme || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
      return;
    const animations = Array.from(pixelsRef.current?.children || []).flatMap((pixel, index) => {
      if (!pixel.animate) return [];
      const from = point(fromTheme, index);
      const to = point(theme, index);
      const angle = index * 2.39996;
      const radius = 2.5 + (index % 7) * 0.65;
      const cloud = { x: 11.5 + Math.cos(angle) * radius, y: 11.5 + Math.sin(angle) * radius };
      const visibleFrom = index < SHAPES[fromTheme].length ? 1 : 0;
      const visibleTo = index < SHAPES[theme].length ? 1 : 0;
      const position = (p, scale = 1) => `translate(${p.x}px, ${p.y}px) scale(${scale})`;
      return [
        pixel.animate(
          [
            { transform: position(from), opacity: visibleFrom },
            { transform: position(from, 0.75), opacity: visibleFrom * 0.8, offset: 0.12 },
            { transform: position(cloud, 0.6), opacity: 0.75, offset: 0.36 },
            {
              transform: position(
                { x: cloud.x + Math.sin(index) * 0.7, y: cloud.y + Math.cos(index) * 0.7 },
                0.65,
              ),
              opacity: 0.65,
              offset: 0.56,
            },
            { transform: position(to, 0.65), opacity: visibleTo * 0.65, offset: 0.8 },
            { transform: position(to), opacity: visibleTo },
          ],
          { duration: 680, delay: (index % 7) * 8, easing: 'linear' },
        ),
      ];
    });
    // The reference's edge passes through a white-pink flash, not a direct blue/gold crossfade.
    if (rimRef.current?.animate) {
      const cold = {
        stroke: '#e6f7ff',
        filter:
          'drop-shadow(0 0 1px #fff) drop-shadow(0 0 3px #9daeff) drop-shadow(0 2px 5px #4767ff)',
      };
      const warm = {
        stroke: '#fffbc3',
        filter:
          'drop-shadow(0 0 1px #fff9ae) drop-shadow(0 0 3px #ffc02d) drop-shadow(0 -2px 5px #ffa700)',
      };
      animations.push(
        rimRef.current.animate(
          [
            fromTheme === 'dark' ? cold : warm,
            {
              stroke: '#fff4f0',
              filter:
                'drop-shadow(0 0 1px #fff) drop-shadow(0 0 3px #ffd4cb) drop-shadow(0 0 5px #eda9d9)',
              offset: 0.48,
            },
            theme === 'dark' ? cold : warm,
          ],
          { duration: 680, easing: 'ease-in-out' },
        ),
      );
    }
    return () => animations.forEach((animation) => animation.cancel());
  }, [theme]);

  return (
    <button
      type="button"
      className={`theme-toggle theme-switch ${isDark ? 'is-dark' : 'is-light'} ${className}`.trim()}
      aria-label={`Switch to ${nextTheme} mode`}
      aria-pressed={!isDark}
      title={`Switch to ${nextTheme} mode`}
      onClick={toggleTheme}
    >
      <span className="theme-switch-track" aria-hidden="true" />
      <span className="theme-switch-thumb" aria-hidden="true">
        <svg className="theme-switch-rim" viewBox="0 0 100 100" fill="none">
          <path
            ref={rimRef}
            className="theme-switch-rim-edge"
            pathLength="100"
            d="M50 5 H65 C82 5 95 18 95 35 V65 C95 82 82 95 65 95 H35 C18 95 5 82 5 65 V35 C5 18 18 5 35 5 Z"
          />
        </svg>
        <svg className="theme-switch-pixels" viewBox="0 0 24 24" fill="currentColor">
          <g ref={pixelsRef}>
            {Array.from({ length: PIXEL_COUNT }, (_, index) => {
              const target = point(theme, index);
              return (
                <rect
                  key={index}
                  width=".8"
                  height=".8"
                  style={{
                    transform: `translate(${target.x}px, ${target.y}px)`,
                    opacity: index < SHAPES[theme].length ? 1 : 0,
                  }}
                />
              );
            })}
          </g>
        </svg>
      </span>
    </button>
  );
}
