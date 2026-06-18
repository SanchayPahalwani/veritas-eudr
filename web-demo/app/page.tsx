import { ScrollProgressRail } from "@/components/ScrollProgressRail";
import { StakesSection } from "@/components/narrative/StakesSection";
import { PipelineSection } from "@/components/narrative/PipelineSection";
import { Band21CaseStudy } from "@/components/narrative/Band21CaseStudy";
import { AreaMathReveal } from "@/components/narrative/AreaMathReveal";
import { EvidenceLedgerSection } from "@/components/narrative/EvidenceLedgerSection";
import { WithheldDdsSection } from "@/components/narrative/WithheldDdsSection";
import { ConsoleSection } from "@/components/console/ConsoleSection";
import { SiteFooter } from "@/components/SiteFooter";

export default function Page() {
  return (
    <>
      <ScrollProgressRail />
      <main className="relative z-10">
        <StakesSection />
        <PipelineSection />
        <Band21CaseStudy />
        <AreaMathReveal />
        <EvidenceLedgerSection />
        <WithheldDdsSection />
        <ConsoleSection />
        <SiteFooter />
      </main>
    </>
  );
}
