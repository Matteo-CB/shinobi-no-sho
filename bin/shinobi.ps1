# Launcher PowerShell explicite (utile si tu veux invoquer .\bin\shinobi.ps1).
# Le .cmd dans le meme dossier suffit dans 99% des cas.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
& (Join-Path $ProjectRoot ".venv\Scripts\python.exe") -m shinobi @args
