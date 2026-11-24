import '../styles/lightPixelEqualizer.css';

// A quiet query signal: motion becomes more active only during a real request.
export default function LightPixelEqualizer({ state = 'idle', standalone = false }) {
  return (
    <div
      className={`ask-light-equalizer${standalone ? ' is-standalone' : ''}`}
      data-state={state}
      aria-hidden="true"
    >
      {Array.from({ length: 9 }, (_, index) => (
        <i
          key={index}
          style={{ '--index': index, '--height': `${5 + (4 - Math.abs(index - 4)) * 3}px` }}
        />
      ))}
    </div>
  );
}
