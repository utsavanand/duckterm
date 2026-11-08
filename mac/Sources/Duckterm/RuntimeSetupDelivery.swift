/// Setup cleanup must not wait forever for an unresponsive web content process.
/// A late or duplicate WebKit completion is harmless after the one-second bound.
@MainActor
enum RuntimeSetupDelivery {
    static func deliver(_ enqueue: (@escaping @MainActor () -> Void) -> Void) async {
        await withCheckedContinuation { continuation in
            let completion = Completion(continuation)
            completion.deadline = Task {
                do { try await Task.sleep(nanoseconds: 1_000_000_000) }
                catch { return }
                completion.finish()
            }
            enqueue { completion.finish() }
        }
    }

    private final class Completion {
        private var continuation: CheckedContinuation<Void, Never>?
        var deadline: Task<Void, Never>?

        init(_ continuation: CheckedContinuation<Void, Never>) {
            self.continuation = continuation
        }

        func finish() {
            guard let continuation else { return }
            self.continuation = nil
            deadline?.cancel()
            deadline = nil
            continuation.resume()
        }
    }
}
