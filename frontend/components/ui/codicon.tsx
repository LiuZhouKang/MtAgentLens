export function Codicon({
  name,
  size = 14,
  className,
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`codicon codicon-${name}${className ? ` ${className}` : ''}`}
      style={{ fontSize: `${size}px` }}
    />
  );
}
