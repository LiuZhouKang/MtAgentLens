// Adapted from the official @sohumsuthar/liquid-glass source:
// https://github.com/sohumsuthar/liquid-glass

export function LiquidGlassFilter({ displacementMap }: { displacementMap?: string | null }) {
  if (!displacementMap) return null;

  return (
    <svg
      style={{
        position: 'absolute',
        width: 0,
        height: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
      aria-hidden="true"
    >
      <filter
        id="lg-refract"
        x="0%"
        y="0%"
        width="100%"
        height="100%"
        filterUnits="objectBoundingBox"
        primitiveUnits="objectBoundingBox"
        colorInterpolationFilters="sRGB"
      >
        <feImage
          href={displacementMap}
          xlinkHref={displacementMap}
          x="0"
          y="0"
          width="1"
          height="1"
          preserveAspectRatio="none"
          result="dispMap"
        />
        <feDisplacementMap
          in="SourceGraphic"
          in2="dispMap"
          scale="0.45"
          xChannelSelector="R"
          yChannelSelector="G"
        />
      </filter>
      <filter
        id="lg-refract-sm"
        x="0%"
        y="0%"
        width="100%"
        height="100%"
        filterUnits="objectBoundingBox"
        primitiveUnits="objectBoundingBox"
        colorInterpolationFilters="sRGB"
      >
        <feImage
          href={displacementMap}
          xlinkHref={displacementMap}
          x="0"
          y="0"
          width="1"
          height="1"
          preserveAspectRatio="none"
          result="dispMap"
        />
        <feDisplacementMap
          in="SourceGraphic"
          in2="dispMap"
          scale="0.45"
          xChannelSelector="R"
          yChannelSelector="G"
        />
      </filter>
    </svg>
  );
}
