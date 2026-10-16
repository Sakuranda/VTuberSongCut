import type { UploadProgress } from "./api";
import { OperationProgress } from "./OperationProgress";

export type FileUploadState = Omit<UploadProgress, "phase"> & {
  phase: UploadProgress["phase"] | "succeeded" | "failed";
  filename: string;
  error?: string;
};

const fileSize = (bytes: number) =>
  bytes >= 1073741824
    ? `${(bytes / 1073741824).toFixed(2)} GB`
    : bytes >= 1048576
      ? `${(bytes / 1048576).toFixed(1)} MB`
      : bytes >= 1024
        ? `${(bytes / 1024).toFixed(1)} KB`
        : `${bytes} B`;

export function UploadFeedback({
  state,
  onRetry,
  disabled = false,
}: {
  state: FileUploadState | null;
  onRetry: () => void;
  disabled?: boolean;
}) {
  if (!state || state.phase === "succeeded") return null;
  return (
    <div className="file-upload-feedback">
      <div className="training-upload-filename">{state.filename}</div>
      <OperationProgress
        title={
          {
            uploading: "正在上传",
            processing: "上传完成，正在检查素材",
            failed: "导入失败",
          }[state.phase]
        }
        percent={
          state.phase === "processing"
            ? 100
            : state.total
              ? Math.min(100, Math.floor((state.loaded / state.total) * 100))
              : null
        }
        failed={state.phase === "failed"}
        detail={
          state.error ||
          (state.phase === "processing"
            ? "正在读取时长和校验文件…"
            : `${fileSize(state.loaded)}${state.total ? ` / ${fileSize(state.total)}` : ""}`)
        }
      />
      {state.phase === "failed" && (
        <button className="button" onClick={onRetry} disabled={disabled}>
          重新选择文件
        </button>
      )}
    </div>
  );
}
