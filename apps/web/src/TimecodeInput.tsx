import { useEffect, useRef, useState } from "react";
import { formatTimecode, parseTimecode } from "./editing";

interface Props {
  id: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  onChange: (ms: number) => void;
  onValidity: (invalid: boolean) => void;
}
export function TimecodeInput({
  id,
  value,
  min,
  max,
  disabled,
  onChange,
  onValidity,
}: Props) {
  const [text, setText] = useState(formatTimecode(value));
  const [error, setError] = useState("");
  const focused = useRef(false);
  const lastValue = useRef(value);
  useEffect(() => {
    if (!focused.current || value !== lastValue.current) {
      setText(formatTimecode(value));
      setError("");
      onValidity(false);
    }
    lastValue.current = value;
  }, [value]);
  function edit(next: string) {
    setText(next);
    const parsed = parseTimecode(next);
    const message =
      parsed === null
        ? "请输入 HH:MM:SS.mmm，例如 00:03:12.500"
        : parsed < min || parsed > max
          ? `有效范围 ${formatTimecode(min)} — ${formatTimecode(max)}`
          : "";
    setError(message);
    onValidity(!!message);
    if (!message && parsed !== null) {
      lastValue.current = parsed;
      onChange(parsed);
    }
  }
  return (
    <>
      <input
        id={id}
        className="timestamp-input"
        type="text"
        spellCheck={false}
        autoComplete="off"
        value={text}
        disabled={disabled}
        placeholder="00:00:00.000"
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : undefined}
        onFocus={() => {
          focused.current = true;
        }}
        onChange={(event) => edit(event.target.value)}
        onBlur={() => {
          focused.current = false;
          if (!error) setText(formatTimecode(value));
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (!error) event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            event.preventDefault();
            setText(formatTimecode(value));
            setError("");
            onValidity(false);
            event.currentTarget.blur();
          }
        }}
      />
      {error && (
        <span className="timestamp-error" id={`${id}-error`} role="alert">
          {error}
        </span>
      )}
    </>
  );
}
