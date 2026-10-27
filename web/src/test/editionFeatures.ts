// Mirrors the backend's edition descriptions (src/duckterm/editions.py); tests
// state the features they rely on rather than assuming Full.
export const LITE = ["folders", "workstreams", "harnesses", "terminal", "shell", "messages", "forks", "session_status"];
export const CODE = [...LITE, "explorer", "file_editing", "git_changes", "inbox", "folder_chat", "checkpoints",
  "summaries", "context", "handoff", "connectors", "pr_review"];
export const FULL = [...CODE.filter(feature => feature !== "pr_review"), "oracle", "remote_workspaces", "artifacts"];
