import { useId } from 'react';
import '../../styles/sourceIllustration.css';

// Decorative art follows real request state; it never simulates progress or row counts.
export default function SourceIllustration({ state = 'idle', variant = 'upload', fileCount = 0 }) {
  const id = useId().replace(/:/g, '');
  const paint = (name) => `url(#${id}-${name})`;
  return (
    <svg
      className={`source-illustration source-illustration--${variant}`}
      data-state={state}
      viewBox="0 0 280 240"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <clipPath id={`${id}-count-window`}>
          <rect x="104" y="195" width="105" height="17" />
        </clipPath>
        <linearGradient id={`${id}-paper`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--art-paper-top)" />
          <stop offset="1" stopColor="var(--art-paper-bottom)" />
        </linearGradient>
        <linearGradient id={`${id}-folder`}>
          <stop stopColor="var(--art-folder-edge)" />
          <stop offset=".38" stopColor="var(--art-folder-mid)" />
          <stop offset=".74" stopColor="var(--art-folder-edge)" />
          <stop offset="1" stopColor="var(--art-folder-end)" />
        </linearGradient>
        <linearGradient id={`${id}-shine`}>
          <stop stopColor="white" stopOpacity="0" />
          <stop offset=".5" stopColor="white" stopOpacity=".12" />
          <stop offset="1" stopColor="white" stopOpacity="0" />
        </linearGradient>
        <filter id={`${id}-shadow`} x="-60%" y="-50%" width="220%" height="220%">
          <feDropShadow dx="0" dy="10" stdDeviation="9" floodOpacity=".22" />
        </filter>
        <g id={`${id}-document`}>
          <path
            d="M93 37h67l30 30v111a9 9 0 0 1-9 9H93a9 9 0 0 1-9-9V46a9 9 0 0 1 9-9Z"
            fill={paint('paper')}
            stroke="var(--art-line)"
            strokeWidth="1.5"
          />
          <path d="M160 38v23a6 6 0 0 0 6 6h23" fill="var(--art-fold)" stroke="var(--art-line)" />
          <path
            d="M99 52h36M99 62h12"
            stroke="var(--art-ink)"
            strokeWidth="3"
            strokeLinecap="round"
          />
          <rect x="114" y="96" width="48" height="61" rx="3" fill="var(--art-grid)" />
          <path
            d="M114 116h48M114 136h48M130 96v61M146 96v61"
            stroke="var(--art-grid-line)"
            strokeWidth="1"
          />
          <rect x="99" y="109" width="33" height="36" rx="3" fill="var(--art-badge)" />
          <path d="m109 118 13 19m0-19-13 19" stroke="var(--art-badge-ink)" strokeWidth="3.5" />
        </g>
      </defs>

      {variant === 'link' ? (
        <g className="source-link" filter={paint('shadow')}>
          <path
            className="source-link-track"
            d="M57 123h166"
            fill="none"
            stroke="var(--art-line)"
            strokeWidth="2"
            strokeDasharray="3 7"
          />
          <path
            className="source-link-packet"
            d="M57 123h166"
            fill="none"
            stroke="var(--art-ink)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray="9 180"
          />
          <g className="source-link-left">
            <rect
              x="43"
              y="72"
              width="66"
              height="98"
              rx="14"
              fill={paint('paper')}
              stroke="var(--art-line)"
            />
            <path
              d="M55 96h41M55 120h41M55 144h41"
              stroke="var(--art-grid)"
              strokeWidth="4"
              strokeLinecap="round"
            />
            <circle cx="91" cy="96" r="2" fill="var(--art-ink)" />
          </g>
          <g className="source-link-right">
            <ellipse
              cx="205"
              cy="84"
              rx="31"
              ry="12"
              fill={paint('paper')}
              stroke="var(--art-line)"
            />
            <path
              d="M174 84v70c0 16 62 16 62 0V84c0 16-62 16-62 0Z"
              fill={paint('folder')}
              stroke="var(--art-line)"
            />
            <path
              d="M174 109c0 16 62 16 62 0m-62 24c0 16 62 16 62 0"
              fill="none"
              stroke="var(--art-line)"
            />
          </g>
          <g
            className="source-link-chain"
            fill="none"
            stroke="var(--art-ink)"
            strokeWidth="5"
            strokeLinecap="round"
          >
            <path d="m137 135-6 6a12 12 0 0 1-17-17l14-14a12 12 0 0 1 17 0" />
            <path d="m143 111 6-6a12 12 0 0 1 17 17l-14 14a12 12 0 0 1-17 0m-7 7 25-25" />
          </g>
          {state === 'success' && (
            <path
              className="source-link-check"
              d="m126 192 10 10 20-22"
              fill="none"
              stroke="var(--art-ink)"
              strokeWidth="4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
        </g>
      ) : (
        <>
          <g className="source-folder-back">
            <path
              d="M61 111h95l11-10h39a10 10 0 0 1 10 10v106H61Z"
              fill="var(--art-folder-end)"
              stroke="var(--art-line)"
            />
            <path d="M73 117h128v87H73Z" fill="var(--art-fold)" />
            <path
              d="M172 109h13m7 0h6"
              stroke="var(--art-ink)"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </g>
          <g className="source-stack" filter={paint('shadow')}>
            <g className="source-sheet source-sheet--left">
              <use href={`#${id}-document`} />
            </g>
            <g className="source-sheet source-sheet--right">
              <use href={`#${id}-document`} />
            </g>
            <g className="source-sheet source-sheet--front">
              <use href={`#${id}-document`} />
            </g>
          </g>
          <g className="source-folder-front" filter={paint('shadow')}>
            <path
              d="M61 129a9 9 0 0 1 9-9h25c10 0 17 14 30 14h81a10 10 0 0 1 10 10v70a10 10 0 0 1-10 10H71a10 10 0 0 1-10-10Z"
              fill={paint('folder')}
              stroke="var(--art-line)"
            />
            <rect
              className="source-folder-shine"
              x="98"
              y="135"
              width="73"
              height="72"
              rx="16"
              fill={paint('shine')}
            />
            <path
              d="m85 184-9 9 9 9m8-18-9 9 9 9"
              fill="none"
              stroke="var(--art-ink)"
              strokeWidth="3"
              opacity=".7"
            />
            <text x="104" y="190" className="source-folder-label">
              {state === 'success' ? 'Imported' : 'Importing'}
            </text>
            <g clipPath={paint('count-window')}>
              {state === 'success' ? (
                <g
                  key={fileCount}
                  className="source-file-counter"
                  style={{ '--file-count': fileCount, '--counter-offset': `${fileCount * -17}px` }}
                >
                  {Array.from({ length: fileCount + 1 }, (_, count) => (
                    <text key={count} x="104" y={207 + count * 17} className="source-folder-detail">
                      {count} {count === 1 ? 'file' : 'files'}
                    </text>
                  ))}
                </g>
              ) : (
                <text x="104" y="207" className="source-folder-detail source-file-count-pending">
                  {fileCount} {fileCount === 1 ? 'file' : 'files'}
                </text>
              )}
            </g>
          </g>
        </>
      )}
    </svg>
  );
}
