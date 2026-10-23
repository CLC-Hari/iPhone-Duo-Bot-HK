# iPhone Duo store monitor

Each check looks up every iPhone Duo storage size and both colors at the same time, then opens a window on top of the desktop with the result. If a store has stock, the PC beeps and the popup includes that model's buy link.

## How to run it

1. Open the folder `C:\Users\ChuHarCC\Desktop\IphoneDUObot`.
2. Double-click `start_monitor.bat`.
3. Leave that window open. It checks once right away, then again every hour.
4. Click **OK** to close each popup.

To start it from a terminal instead, open PowerShell in that folder and run:

```powershell
python monitor.py
```

Press `Ctrl+C` in that window when you want it to stop.
