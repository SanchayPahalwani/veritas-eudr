/** The scrollytelling spine. Drives the progress rail and section anchors. */
export interface Chapter {
  id: string;
  index: string;
  label: string;
}

export const CHAPTERS: Chapter[] = [
  { id: "stakes", index: "00", label: "Stakes" },
  { id: "pipeline", index: "01", label: "Pipeline" },
  { id: "band21", index: "02", label: "Band-21" },
  { id: "area", index: "03", label: "Area math" },
  { id: "ledger", index: "04", label: "Evidence" },
  { id: "dds", index: "05", label: "The DDS" },
  { id: "console", index: "06", label: "Console" },
];
