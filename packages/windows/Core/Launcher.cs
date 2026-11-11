using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace DuckTerm.Windows;

public sealed class LauncherException(string message) : Exception(message);
public sealed record Command(string FileName, IReadOnlyList<string> Arguments, string? Input = null, bool UnicodeOutput = false);
public sealed record CommandResult(int ExitCode, string Output);

public interface ICommandRunner
{
    Task<CommandResult> RunAsync(Command command, CancellationToken cancellationToken);
}

public sealed class CommandRunner : ICommandRunner
{
    public async Task<CommandResult> RunAsync(Command command, CancellationToken cancellationToken)
    {
        var info = new ProcessStartInfo(command.FileName)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = command.UnicodeOutput ? Encoding.Unicode : Encoding.UTF8,
        };
        foreach (var argument in command.Arguments) info.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = info };
        try { process.Start(); }
        catch (System.ComponentModel.Win32Exception) { throw new LauncherException("WSL could not start. Install WSL 2, then run this command again."); }
        try
        {
            var output = process.StandardOutput.ReadToEndAsync(cancellationToken);
            var error = process.StandardError.ReadToEndAsync(cancellationToken);
            if (command.Input is not null) await process.StandardInput.WriteAsync(command.Input.AsMemory(), cancellationToken);
            process.StandardInput.Close();
            await process.WaitForExitAsync(cancellationToken);
            // Stderr may contain paths or authentication output; never forward it to the UI/log.
            await error;
            return new(process.ExitCode, await output);
        }
        catch
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            throw;
        }
    }
}

public sealed class Workspace
{
    public const int Port = 4317;
    public static readonly Uri Origin = new($"http://127.0.0.1:{Port}/");
    public required string State { get; init; }
    public string? WheelName { get; init; }
    internal string? Token { get; init; }
    public string? ErrorCode { get; init; }
    public bool Running => State == "running";
    public static bool IsLocal(Uri uri) => uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port == Port && uri.UserInfo.Length == 0;
    public override string ToString() => $"DuckTerm Lite: {State}"; // Never serialize the login token.

    public static Workspace Parse(string text)
    {
        try
        {
            using var document = JsonDocument.Parse(text);
            var value = document.RootElement;
            if (value.GetProperty("protocol").GetInt32() != 1) throw new JsonException();
            if (!value.GetProperty("ok").GetBoolean())
            {
                var code = value.GetProperty("code").GetString();
                // Display only controlled protocol errors, never an arbitrary subprocess response.
                throw new LauncherException(code switch
                {
                    "root_user" => "Choose a WSL distribution with a regular user. DuckTerm cannot run as root.",
                    "wsl2_required" => "DuckTerm requires WSL 2. Convert the selected distribution with wsl --set-version <name> 2.",
                    "missing_dependencies" => "Install the WSL prerequisites listed in the Windows package README, then retry.",
                    "not_installed" => "Install the bundled backend first: DuckTerm.Windows.Cli install --distro <name>.",
                    "port_conflict" or "identity_conflict" => "Port 4317 belongs to another or unverified workspace. DuckTerm left it running.",
                    "workspace_conflict" or "unsafe_directory" or "unsafe_file" => "An existing directory or file is not owned by this Windows package. No existing workspace was adopted.",
                    "busy" => "Another Windows installation or launch is in progress. Try again shortly.",
                    "launch_timeout" => "The backend is still starting. Run status before trying again.",
                    "launch_failed" => "The backend exited. Inspect ~/.duckterm-windows-package/server.log inside WSL.",
                    _ => "The WSL operation failed. Run doctor and consult the Windows package README.",
                });
            }
            var state = value.GetProperty("state").GetString() ?? throw new JsonException();
            if (state is not ("running" or "stopped" or "installed" or "not_installed" or "ready" or "missing_dependencies")) throw new JsonException();
            string? token = value.TryGetProperty("token", out var tokenValue) ? tokenValue.GetString() : null;
            if (token is not null && (!Regex.IsMatch(token, "^[A-Za-z0-9_-]{32,128}$") || state != "running")) throw new JsonException();
            if (state == "running" && (!Uri.TryCreate(value.GetProperty("url").GetString(), UriKind.Absolute, out var uri) || !IsLocal(uri) || uri.AbsolutePath != "/" || uri.Query.Length != 0 || uri.Fragment.Length != 0)) throw new JsonException();
            return new Workspace { State = state, Token = token, WheelName = value.TryGetProperty("wheel_name", out var wheel) ? wheel.GetString() : null };
        }
        catch (Exception exception) when (exception is JsonException or InvalidOperationException or KeyNotFoundException or FormatException)
        { throw new LauncherException("WSL did not return a valid launcher response. Check that Python 3.11+ is installed in the selected distribution."); }
    }

    public string LoginToken() => Running && Token is not null ? Token : throw new LauncherException("The workspace is not ready to open.");
    public Uri BrowserLogin() => new(Origin, "login?token=" + Uri.EscapeDataString(LoginToken()));

    public async Task VerifyWindowsConnectionAsync(CancellationToken cancellationToken)
    {
        using var handler = new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false };
        using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(5) };
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(Origin, "edition"));
        request.Headers.Add("X-Duckterm-Token", LoginToken());
        try
        {
            using var response = await client.SendAsync(request, cancellationToken);
            if (response.StatusCode != HttpStatusCode.OK) throw new JsonException();
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
            var value = document.RootElement;
            var identity = value.GetProperty("instance");
            if (value.GetProperty("id").GetString() != "lite" || identity.GetProperty("instance_id").GetString() != "windows-lite" || identity.GetProperty("port").GetInt32() != Port) throw new JsonException();
        }
        catch (Exception exception) when (exception is HttpRequestException or JsonException or KeyNotFoundException or InvalidOperationException or TaskCanceledException)
        { throw new LauncherException("The WSL backend started, but Windows cannot reach its workspace. Check WSL localhost forwarding and whether Windows port 4317 is occupied."); }
    }
}

public sealed class Launcher(ICommandRunner runner, string? wslPath = null)
{
    private readonly string executable = wslPath ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wsl.exe");
    public async Task<string[]> DistributionsAsync(CancellationToken cancellationToken)
    {
        var result = await runner.RunAsync(new(executable, ["--list", "--quiet"], UnicodeOutput: true), cancellationToken);
        if (result.ExitCode != 0) throw new LauncherException("WSL is unavailable. Run wsl --install -d Ubuntu-24.04, finish setup, and retry.");
        return result.Output.TrimStart('\uFEFF').Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).Select(x => x.Trim()).Where(x => x.Length > 0).ToArray();
    }

    public static string ChooseDistribution(IEnumerable<string> installed, string? requested)
    {
        var choices = installed.Where(x => !x.StartsWith("docker-desktop", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (requested is not null && choices.Contains(requested, StringComparer.Ordinal)) return requested;
        if (requested is not null) throw new LauncherException("The selected WSL distribution is not installed. Use the exact name from the list command.");
        if (choices.Length == 1) return choices[0];
        throw new LauncherException(choices.Length == 0 ? "No user WSL distribution is installed. Run wsl --install -d Ubuntu-24.04, then finish Ubuntu setup." : "More than one WSL distribution is installed. Choose one with --distro <name>.");
    }

    public async Task<Workspace> InvokeAsync(string action, string? distro, string packageDirectory, CancellationToken cancellationToken)
    {
        if (action is not ("doctor" or "install" or "status" or "start" or "restart")) throw new ArgumentException("Unknown launcher action.");
        var selected = ChooseDistribution(await DistributionsAsync(cancellationToken), distro);
        var request = new Dictionary<string, object> { ["protocol"] = 1, ["action"] = action };
        if (action == "install")
        {
            var payload = Path.Combine(packageDirectory, "payload");
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(payload, "manifest.json"), cancellationToken));
            var manifest = document.RootElement;
            var name = manifest.GetProperty("wheel_name").GetString() ?? "";
            var digest = manifest.GetProperty("sha256").GetString() ?? "";
            if (manifest.GetProperty("protocol").GetInt32() != 1 || !Regex.IsMatch(name, @"^duckterm-[0-9][A-Za-z0-9.+]*-py3-none-any\.whl$") || !Regex.IsMatch(digest, "^[a-f0-9]{64}$")) throw new LauncherException("The backend payload manifest is invalid.");
            var wheel = Path.Combine(payload, name);
            if (new FileInfo(wheel).Length > 32 * 1024 * 1024) throw new LauncherException("The backend payload is too large.");
            var bytes = await File.ReadAllBytesAsync(wheel, cancellationToken);
            if (Convert.ToHexStringLower(SHA256.HashData(bytes)) != digest) throw new LauncherException("The backend payload checksum does not match. Download a fresh Windows package.");
            request["wheel_name"] = name;
            request["sha256"] = digest;
            request["wheel_base64"] = Convert.ToBase64String(bytes);
        }
        using var source = typeof(Launcher).Assembly.GetManifestResourceStream("controller.py")!;
        using var memory = new MemoryStream();
        await source.CopyToAsync(memory, cancellationToken);
        var code = "import base64;exec(base64.b64decode('" + Convert.ToBase64String(memory.ToArray()) + "'))";
        var result = await runner.RunAsync(new(executable, ["--distribution", selected, "--exec", "python3", "-I", "-c", code], JsonSerializer.Serialize(request)), cancellationToken);
        var workspace = Workspace.Parse(result.Output);
        if (result.ExitCode != 0) throw new LauncherException("The WSL operation failed.");
        return workspace;
    }
}
