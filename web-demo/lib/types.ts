/**
 * TypeScript mirrors of the veritas-eudr Pydantic domain models
 * (src/veritas_eudr/domain.py). These shapes are byte-identical to the live
 * API responses — see scripts/export_demo_data.py.
 */

export type Disposition = "AUTO_VALID" | "AUTO_FIXED" | "NEEDS_REVIEW";
export type Severity = "INFO" | "WARNING" | "ERROR";
export type RiskTier = "low" | "high" | "more-info-needed";
export type RequiredGeometryFormat = "point" | "polygon";
export type SamplingStrategy = "point" | "zonal_majority" | "fractional_overlap";

export interface Finding {
  rule_id: string;
  severity: Severity;
  disposition: Disposition;
  human_reason: string;
  failing_coordinate: [number, number] | null;
  details?: Record<string, unknown>;
}

export interface ValidationReport {
  plot_id: string;
  source_geometry_type: string;
  findings: Finding[];
  repaired_geometry_wkt: string | null;
  notes: string[];
  disposition: Disposition;
  needs_review: boolean;
}

export interface AreaMeasurement {
  measured_area_ha: number;
  area_ha_ease6933: number;
  area_ha_local_utm: number | null;
  area_ha_webmercator: number | null;
  delta_6933_pct: number;
  delta_webmercator_pct: number | null;
  required_geometry_format: RequiredGeometryFormat;
  borderline: boolean;
  area_authority: string;
}

export interface LayerSample {
  dataset_name: string;
  dataset_version: string;
  layer: string;
  strategy: SamplingStrategy;
  value: number | null;
  covered_fraction: number | null;
  covered_ha: number | null;
  details: Record<string, unknown>;
  note: string | null;
}

export interface EvidenceRecord {
  id?: number;
  run_id: string;
  plot_id: string;
  dataset_name: string;
  dataset_version: string;
  rule_id: string;
  pixel_value: number | null;
  covered_fraction: number | null;
  verdict: string;
  ts: string;
}

export interface RiskProfile {
  plot_id: string;
  risk: RiskTier;
  rationale: string;
  axes: LayerSample[];
  evidence: EvidenceRecord[];
  cutoff_date: string;
  boundary_uncertain: boolean;
  whisp_risk_pcrop: string;
}

/** Response shape of GET /plots/{plot_id}/risk. */
export interface PlotRisk {
  plot_id: string;
  validation: ValidationReport;
  area: AreaMeasurement | null;
  risk: RiskProfile | null;
  assessed: boolean;
}

export interface DueDiligenceStatement {
  consignment_id: string;
  operator_name: string;
  commodity: string;
  plot_ids: string[];
  geojson: { type: string; features: unknown[] };
  deforestation_determination: RiskTier;
  legality_status: "NOT_ASSESSED";
  compliance_complete: boolean;
  due_diligence_path: string;
  country_risk_class: string;
  due_diligence_regime: string;
  reference_number: string | null;
  verification_number: string | null;
  valid_from: string | null;
  valid_until: string | null;
  annual_review_required: boolean;
  geojson_spec_version: string;
  policy_version: string;
  deforestation_cutoff_date: string;
  regulation_application_date: string;
  generated_at: string;
}

/** Properties carried by each feature in plots.geojson. */
export interface PlotFeatureProps {
  plot_id: string;
  risk: RiskTier | null;
  disposition: Disposition;
  boundary_uncertain: boolean;
  rationale: string;
  n_evidence: number;
}

export interface PlotsIndex {
  run_id: string;
  counts: Record<string, number>;
  band21_plot_ids: string[];
  hero_plot_id: string;
  aoi_center: [number, number];
  n_plots: number;
}

export interface ValidationCase {
  scenario: string;
  title: string;
  blurb: string;
  plot_id: string;
  source_geometry_type: string;
  disposition: Disposition;
  needs_review: boolean;
  findings: Array<{
    rule_id: string;
    severity: Severity;
    disposition: Disposition;
    human_reason: string;
    failing_coordinate: [number, number] | null;
  }>;
}

export interface AreaDemo extends AreaMeasurement {
  aoi_lat: number;
  sec2_lat_factor: number;
}

export interface Manifest {
  run_id: string;
  generated_at: string;
  policy_version: string;
  operator_name: string;
  consignment_id: string;
  source_fixtures: string[];
  counts: Record<string, number>;
}

export interface EvidenceLedger {
  run_id: string;
  evidence: EvidenceRecord[];
}
