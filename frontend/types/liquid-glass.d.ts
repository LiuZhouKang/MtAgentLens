declare module '@sohumsuthar/liquid-glass' {
  import type { CSSProperties, ReactNode } from 'react';

  export function LiquidGlass(props: {
    macro?: boolean;
    mobileFlat?: boolean;
    className?: string;
    style?: CSSProperties;
    contentClassName?: string;
    contentStyle?: CSSProperties;
    children?: ReactNode;
    [key: string]: unknown;
  }): ReactNode;

  export function LiquidGlassFilter(props: { displacementMap?: string | null }): ReactNode;
}

declare module '@sohumsuthar/liquid-glass/hooks' {
  export function useLiquidGlassEffects(opts?: {
    cursor?: boolean;
    spotlight?: boolean;
    reveal?: boolean;
    scroll?: boolean;
    routeKey?: string;
  }): void;
}

declare module '@sohumsuthar/liquid-glass/components/LiquidGlass.jsx' {
  import type { CSSProperties, ReactNode } from 'react';

  export default function LiquidGlass(props: {
    macro?: boolean;
    mobileFlat?: boolean;
    className?: string;
    style?: CSSProperties;
    contentClassName?: string;
    contentStyle?: CSSProperties;
    children?: ReactNode;
    [key: string]: unknown;
  }): ReactNode;
}

declare module '@sohumsuthar/liquid-glass/components/LiquidGlassFilter.jsx' {
  import type { ReactNode } from 'react';

  export default function LiquidGlassFilter(props: { displacementMap?: string | null }): ReactNode;
}
