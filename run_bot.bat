@echo off
REM === Altere o caminho abaixo se sua pasta for outra ===
cd /d C:\projeto

REM Ativa o venv
call .\.venv\Scripts\activate.bat

REM --- Opção A: usar a chave só nesta janela ---
REM Substitua pela sua chave real entre aspas (ou deixe comentado se já setou no sistema)
REM set "OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx"

REM --- Opção B: ler a chave de um arquivo (mais seguro) ---
REM Crie um arquivo C:\projeto\key.txt contendo APENAS a chave em 1 linha
if exist key.txt (
  for /f "usebackq delims=" %%K in ("key.txt") do set "OPENAI_API_KEY=%%K"
)

REM Sobe o servidor
python app.py

REM Mantém a janela aberta se der erro
echo.
echo (Pressione qualquer tecla para fechar)
pause >nul
