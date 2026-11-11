using System.Text.Json;
using DuckTerm.Windows;

var tests = new List<(string, Action)>
{
    ("does not select a Docker distribution", () => Equal("Ubuntu", Launcher.ChooseDistribution(["docker-desktop", "Ubuntu"], null))),
    ("does not guess among user distributions", () => Reject(() => Launcher.ChooseDistribution(["Ubuntu", "Debian"], null))),
    ("unknown distribution is rejected", () => Reject(() => Launcher.ChooseDistribution(["Ubuntu"], "Ubuntu --user root"))),
    ("exact names with spaces remain intact", () => Equal("My Ubuntu", Launcher.ChooseDistribution(["My Ubuntu"], "My Ubuntu"))),
    ("foreign and lookalike origins are rejected", () =>
    {
        foreach (var url in new[] { "http://127.0.0.1:4300/", "https://127.0.0.1:4317/", "http://127.0.0.1.evil.test:4317/", "http://user@127.0.0.1:4317/", "file:///tmp/report.html" })
            Equal(false, Workspace.IsLocal(new Uri(url)));
    }),
    ("malformed subprocess output does not reach diagnostics", () =>
    {
        foreach (var output in new[] { "secret-value", "{}", "null", "[]", "{\"protocol\":2,\"ok\":true}" })
            Reject(() => Workspace.Parse(output));
    }),
    ("helper errors cannot reflect secrets", () =>
    {
        try { Workspace.Parse("{\"protocol\":1,\"ok\":false,\"code\":\"unknown\",\"message\":\"secret-value\"}"); }
        catch (LauncherException error) { Equal(false, error.Message.Contains("secret-value")); return; }
        throw new Exception("expected failure");
    }),
    ("status never requires or prints a credential", () =>
    {
        var state = Workspace.Parse("{\"protocol\":1,\"ok\":true,\"state\":\"running\",\"url\":\"http://127.0.0.1:4317\"}");
        Equal("DuckTerm Lite: running", state.ToString());
        Reject(() => state.LoginToken());
    }),
    ("login uses only the fixed origin", () =>
    {
        var token = new string('a', 43);
        var state = Workspace.Parse(JsonSerializer.Serialize(new { protocol = 1, ok = true, state = "running", url = "http://127.0.0.1:4317", token }));
        Equal(false, state.ToString().Contains(token));
        Equal("127.0.0.1", state.BrowserLogin().Host);
        Equal("/login", state.BrowserLogin().AbsolutePath);
        Reject(() => Workspace.Parse(JsonSerializer.Serialize(new { protocol = 1, ok = true, state = "running", url = "https://example.com/", token })));
    }),
};
foreach (var (name, test) in tests) { test(); Console.WriteLine("PASS " + name); }

var fake = new FakeRunner();
var launcher = new Launcher(fake, "wsl.exe");
await launcher.InvokeAsync("status", "My Ubuntu", ".", CancellationToken.None);
Equal(2, fake.Commands.Count);
Equal(true, fake.Commands[0].UnicodeOutput);
var invocation = fake.Commands[1];
Equal("My Ubuntu", invocation.Arguments[1]);
Equal("--exec", invocation.Arguments[2]);
Equal("python3", invocation.Arguments[3]);
Equal("-I", invocation.Arguments[4]);
Equal("status", JsonDocument.Parse(invocation.Input!).RootElement.GetProperty("action").GetString());
Equal(false, invocation.Arguments.Any(x => x == "bash" || x == "sh"));
Console.WriteLine("PASS WSL request preserves arguments and sends data over stdin without a shell");
Console.WriteLine($"{tests.Count + 1} Windows launcher contracts passed.");
return 0;

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual)) throw new Exception($"Expected {expected}; got {actual}");
}
static void Reject(Action action)
{
    try { action(); } catch (LauncherException) { return; }
    throw new Exception("Expected a safe rejection");
}
sealed class FakeRunner : ICommandRunner
{
    public List<Command> Commands { get; } = [];
    public Task<CommandResult> RunAsync(Command command, CancellationToken cancellationToken)
    {
        Commands.Add(command);
        return Task.FromResult(new CommandResult(0, Commands.Count == 1 ? "\uFEFFMy Ubuntu\r\n" : "{\"protocol\":1,\"ok\":true,\"state\":\"stopped\"}"));
    }
}
