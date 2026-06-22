'use client';

import type { ButtonHTMLAttributes, ReactNode } from 'react';

type PrismButtonVariant = 'primary' | 'secondary' | 'ghost';
type PrismButtonSize = 'sm' | 'md';

type PrismButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  variant?: PrismButtonVariant;
  size?: PrismButtonSize;
};

function sizeStyle(size: PrismButtonSize) {
  if (size === 'sm') {
    return {
      minHeight: 36,
      padding: '8px 12px',
      fontSize: 11,
    };
  }

  return {
    minHeight: 44,
    padding: '10px 16px',
    fontSize: 12,
  };
}

export default function PrismButton({
  children,
  className,
  variant = 'secondary',
  size = 'md',
  type = 'button',
  style,
  ...props
}: PrismButtonProps) {
  return (
    <button
      type={type}
      className={['prism-button', `prism-button--${variant}`, className].filter(Boolean).join(' ')}
      style={{
        ...sizeStyle(size),
        ...style,
      }}
      {...props}
    >
      {children}
    </button>
  );
}
