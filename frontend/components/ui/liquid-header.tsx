'use client';
import React from 'react';

type SpectraTone = 
  | 'default' | 'ocean' | 'ultra' | 'ultravioet' 
  | 'chrome' | 'flux' | 'danger' | 'warning' | 'info' | 'success';

export function LiquidHeader({ tone = 'default', opacity = 0.8 }: { tone?: SpectraTone, opacity?: number }) {
  // Mapping the EXACT palettes from the user's SPECTRA image
  const palettes: Record<SpectraTone, string[]> = {
    // ORIGINAL (Pink/Orange/Peach)
    default: ['#ff7171', '#ffb86c', '#f06292'],
    // OCEAN (Light Blue/Cyan/Deep Blue)
    ocean: ['#00c6ff', '#0072ff', '#4facfe'],
    // ULTRA (Dark Blue/Purple)
    ultra: ['#1e3c72', '#2a5298', '#667eea'],
    // ULTRAVIOLET (Violet/Light Purple/White)
    ultravioet: ['#b388ff', '#8a2be2', '#e1bee7'],
    // CHROME (Silver/Gray/Dark Gray)
    chrome: ['#e0e0e0', '#9e9e9e', '#616161'],
    // FLUX (Red/Orange/Yellow)
    flux: ['#ff0844', '#ffb199', '#f78ca0'],
    
    // Semantic mappings for UI states
    danger: ['#ff0844', '#ff3366', '#ffb199'], // Similar to flux but more red
    warning: ['#f6d365', '#fda085', '#ff9a44'],
    info: ['#00c6ff', '#4facfe', '#00f2fe'], // Similar to ocean
    success: ['#43e97b', '#38f9d7', '#10b981'],
  };

  const colors = palettes[tone] || palettes.default;

  return (
    <div className="spectra-container" style={{ opacity }}>
      <div className="spectra-blob b1" style={{ backgroundColor: colors[0] }} />
      <div className="spectra-blob b2" style={{ backgroundColor: colors[1] }} />
      <div className="spectra-blob b3" style={{ backgroundColor: colors[2] }} />
      <div className="spectra-overlay" />
    </div>
  );
}
