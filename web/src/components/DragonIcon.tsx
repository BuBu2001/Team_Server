export function DragonIcon({
  size = 32,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M52 18c-2-4-6-6-10-6-1 0-2 0-3 1-2-3-5-5-9-5-3 0-6 1-8 4-1-1-3-1-4-1-4 0-7 2-9 6-2 4-2 9 0 13 1 2 2 3 4 4-1 3 0 6 2 8 2 2 5 3 8 2 1 2 3 4 5 5 3 1 6 1 8-1 2 1 4 1 6 0 3-1 5-4 5-7 0-1 0-2-1-3 3-1 5-4 5-7 0-2-1-4-2-5 2-2 3-5 1-8z"
        fill="currentColor"
      />
      <path d="M20 22c-1 0-2 1-2 2s1 2 2 2 2-1 2-2-1-2-2-2z" fill="#0a0a0a" />
      <path d="M42 22c-1 0-2 1-2 2s1 2 2 2 2-1 2-2-1-2-2-2z" fill="#0a0a0a" />
      <path
        d="M28 32c2 2 6 2 8 0"
        stroke="#0a0a0a"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M14 14c-2-2-4-2-6-1 2 1 3 3 3 5 1-2 2-3 3-4z"
        fill="currentColor"
        opacity="0.85"
      />
      <path
        d="M50 14c2-2 4-2 6-1-2 1-3 3-3 5-1-2-2-3-3-4z"
        fill="currentColor"
        opacity="0.85"
      />
      <path
        d="M24 10c-1-2-3-3-5-2 2 0 3 2 3 4 1-1 1-1 2-2z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M40 10c1-2 3-3 5-2-2 0-3 2-3 4-1-1-1-1-2-2z"
        fill="currentColor"
        opacity="0.6"
      />
    </svg>
  );
}
