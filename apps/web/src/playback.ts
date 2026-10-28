import type { PreviewQuality, Project } from "./api";

export function previewReady(
  project: Project,
  quality: PreviewQuality,
): boolean {
  if (!project.has_video) return true;
  return quality === "low"
    ? !!project.preview_file && project.preview_profile === "low320-v1"
    : !!project.high_preview_file;
}

// Preview completion is metadata, not permission to overwrite an editor draft.
export function mergePreviewMetadata(
  current: Project,
  fresh: Project,
): Project {
  if (current.id !== fresh.id) return current;
  return {
    ...current,
    preview_file: fresh.preview_file,
    preview_profile: fresh.preview_profile,
    high_preview_file: fresh.high_preview_file,
    source_width: fresh.source_width,
    source_height: fresh.source_height,
    source_browser_compatible: fresh.source_browser_compatible,
  };
}
