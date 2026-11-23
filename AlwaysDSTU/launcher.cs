// Небольшой Windows-запускатель: использует исходники и окружение рядом с собой.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static void Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string application = Path.Combine(folder, "main.py");
        string[] interpreters = {
            Path.Combine(folder, ".venv", "Scripts", "python.exe"),
            Path.Combine(folder, ".venv", "bin", "python.exe")
        };

        if (!File.Exists(application))
        {
            ShowError("Не найден файл main.py. Поместите запускатель в папку AlwaysDSTU рядом с файлами программы.");
            return;
        }

        string interpreter = null;
        foreach (string candidate in interpreters)
        {
            if (File.Exists(candidate))
            {
                interpreter = candidate;
                break;
            }
        }

        if (interpreter == null)
        {
            ShowError("Не найдено окружение Python в папке .venv. Настройте приложение по инструкции AlwaysDSTU/README.md.");
            return;
        }

        try
        {
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = interpreter;
            start.Arguments = "\"" + application + "\"";
            start.WorkingDirectory = folder;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            Process.Start(start);
        }
        catch (Exception error)
        {
            ShowError("Не удалось открыть приложение: " + error.Message);
        }
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "Always DSTU", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
