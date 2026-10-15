export function OperationProgress({
  title,
  percent,
  detail,
  failed = false,
}: {
  title: string;
  percent: number | null;
  detail: string;
  failed?: boolean;
}) {
  return (
    <div className={`operation-progress${failed ? " failed" : ""}`}>
      <div className="operation-progress-heading">
        <strong>{title}</strong>
        <span>{percent === null ? "处理中…" : `${percent}%`}</span>
      </div>
      <progress aria-label={title} max={100} value={percent ?? undefined} />
      <p role={failed ? "alert" : "status"}>{detail}</p>
    </div>
  );
}
