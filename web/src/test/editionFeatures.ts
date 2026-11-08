// Mirrors the backend's edition descriptions (src/duckterm/editions.py); tests
// state the features they rely on rather than assuming Full.
export const LITE = ["folders", "workstreams", "harnesses", "terminal", "shell", "messages", "forks", "session_status",
  "inbox", "checkpoints", "summaries", "context", "handoff", "artifacts", "overview"];
export const CODE = [...LITE, "explorer", "file_editing", "git_changes", "folder_chat", "connectors", "pr_review", "rule_suggestions"];
export const FULL = [...CODE, "oracle", "remote_workspaces"];
