export function MergeMemoryDisclosure({ source, target }: { source: string; target: string }) {
  return <section className="rd-merge-memory">
    <h3>Source memory access</h3>
    <p>{target} will be able to read {source}’s available conversation history, saved summaries, session notes, tasks, events, and files saved as artifacts.</p>
    <p>This also includes {source}’s inbox messages with you and other agents in its folder, even when {target} is in a different folder. This access remains after {source} closes and is not limited to the summary you send.</p>
  </section>;
}
