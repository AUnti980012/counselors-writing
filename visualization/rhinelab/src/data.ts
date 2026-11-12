import content from "../content/archives.json" with { type: "json" };

export interface ArchiveRecord {
  id: string;
  title: string;
  en: string;
  department: string;
  category: string;
  date: string;
  lead: string;
  clearance: string;
  abstract: string;
  findings: string[];
  source: string;
  empty?: true;
}

export const categories = ["全部档案", ...content.categories];
export const archiveColumns = content.columns;
export const records: ArchiveRecord[] = [...content.records];

const indexesByLane = archiveColumns.map((category, lane) => {
  const indexes = records.flatMap((record, index) =>
    record.category === category ? [index] : [],
  );
  if (indexes.length) return indexes;
  indexes.push(records.length);
  records.push({
    id: `EMPTY-${lane + 1}`,
    title: "暂无档案",
    en: "EMPTY ARCHIVE",
    department: "—",
    category,
    date: "—",
    lead: "—",
    clearance: "暂无档案",
    abstract: "这类档案会在下次导出知识库后更新。",
    findings: [],
    source: "",
    empty: true,
  });
  return indexes;
});
const locations = records.map((record, index) => {
  const lane = archiveColumns.indexOf(record.category);
  return { lane, row: 12 + indexesByLane[lane].indexOf(index) };
});

export function columnFiles(lane: number) {
  return indexesByLane[lane];
}
export function fileLocation(index: number) {
  const { lane, row } = locations[index];
  return { lane, row, slot: lane * 32 + row };
}
