import { LandingNav } from '@/components/landing/landing-nav';
import { LandingHero } from '@/components/landing/landing-hero';
import { LandingBrandMoment } from '@/components/landing/landing-brand-moment';
import { LandingPipeline } from '@/components/landing/landing-pipeline';
import { LandingLiveDemo } from '@/components/landing/landing-live-demo';
import { LandingValues } from '@/components/landing/landing-values';
import { LandingCta } from '@/components/landing/landing-cta';

/**
 * / — the MtAgentLens landing entry page: a six-section scroll narrative.
 *  ① HERO (particle universe + morph) → ② brand moment → ③ pipeline →
 *  ④ real demo graph → ⑤ value pillars → ⑥ CTA / footer.
 * Sections live directly under the root <main> (no nested <main>).
 */
export default function LandingPage() {
  return (
    <>
      <LandingNav />
      <LandingHero />
      <LandingBrandMoment />
      <LandingPipeline />
      <LandingLiveDemo />
      <LandingValues />
      <LandingCta />
    </>
  );
}
