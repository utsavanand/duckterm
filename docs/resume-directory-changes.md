# Resume after a project directory changes

DuckTerm associates a session with its recorded conversation ID. If a Claude conversation started in a parent directory and later worked in a child directory, its transcript may still be stored under the original project. DuckTerm now searches for that exact ID when the current project has no matching file. The named DuckTerm session and current working directory stay unchanged.

Cross-project native resume requires **Claude Code 2.1.223 or later**. Older CLIs cannot be made cross-project-capable by DuckTerm locating a file; update Claude Code before using this path. The provider documents the lookup order in [Manage sessions](https://code.claude.com/docs/en/sessions). DuckTerm continues to pass the recorded ID to `--resume`, never `--continue` or a guessed newest conversation.

The fallback examines at most 2,000 project directory entries and refuses an incomplete search. It requires one exact filename, consistent conversation identity, original project metadata and user/assistant message evidence in the existing 256 KiB head/tail windows. Tool-content messages count. Missing metadata outside those windows can cause a conservative refusal.

Two exact-name files block fallback even if one is empty. This is deliberately stricter than the provider's documented message-bearing-copy rule. DuckTerm does not delete or select among duplicates. The existing current-project shortcut remains unchanged; the new fallback checks do not imply that every native transcript has been fully validated.

Known-ID lookup and recovery status use this fallback. The owner-adoption picker still searches only the selected session's project. If identity disappears between the Resume check and command construction, DuckTerm refuses the launch instead of starting a new conversation. No schema, transcript rewrite, copy, or automatic session restart is involved.
