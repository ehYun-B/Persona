param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name "UDL_Persona" `
    --add-data "research_app;research_app" `
    --add-data "scripts;scripts" `
    --add-data "agents;agents" `
    --add-data "reports;reports" `
    launch_research_app.py

Write-Host "완료: dist\UDL_Persona\UDL_Persona.exe"
