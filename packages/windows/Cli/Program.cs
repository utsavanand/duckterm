using System.Diagnostics;
using DuckTerm.Windows;

if (!OperatingSystem.IsWindows()) { Console.Error.WriteLine("This launcher runs on Windows. Its backend runs inside WSL 2."); return 1; }
var action = args.FirstOrDefault() ?? "help";
if (action is "help" or "--help")
{
    Console.WriteLine("DuckTerm.Windows.Cli list | doctor | install | status | start | restart [--distro <name>]");
    Console.WriteLine("start/restart open the Windows browser. Closing it leaves agent sessions running.");
    return 0;
}
string? distro = null;
if (args.Length == 3 && args[1] == "--distro") distro = args[2];
else if (args.Length != 1) { Console.Error.WriteLine("Use --distro <exact WSL distribution name>."); return 1; }
if (action is not ("list" or "doctor" or "install" or "status" or "start" or "restart")) { Console.Error.WriteLine("Unknown command. Run help."); return 1; }
using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(5));
Console.CancelKeyPress += (_, e) => { e.Cancel = true; timeout.Cancel(); };
try
{
    var launcher = new Launcher(new CommandRunner());
    if (action == "list")
    {
        foreach (var name in await launcher.DistributionsAsync(timeout.Token)) Console.WriteLine(name);
        return 0;
    }
    var workspace = await launcher.InvokeAsync(action, distro, AppContext.BaseDirectory, timeout.Token);
    Console.WriteLine(workspace); // Deliberately omit credentials and raw helper output.
    if (workspace.State == "missing_dependencies")
    {
        Console.Error.WriteLine("Inside Ubuntu: sudo apt-get update && sudo apt-get install -y python3 python3-venv tmux jq curl git lsof");
        return 1;
    }
    if (action is "start" or "restart")
    {
        await workspace.VerifyWindowsConnectionAsync(timeout.Token);
        Process.Start(new ProcessStartInfo(workspace.BrowserLogin().AbsoluteUri) { UseShellExecute = true });
    }
    return 0;
}
catch (LauncherException error) { Console.Error.WriteLine(error.Message); return 1; }
catch (OperationCanceledException) { Console.Error.WriteLine("Operation cancelled or timed out. Run status before retrying."); return 1; }
catch (Exception error) when (error is IOException or System.Text.Json.JsonException or System.ComponentModel.Win32Exception)
{ Console.Error.WriteLine("The Windows package is incomplete or unavailable. Extract a fresh package and retry."); return 1; }
