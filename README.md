# Compressed-air Diagnostics Analytics & Statistics Hub (C-DASH)

A [Dash](https://dash.plotly.com/) web application (`cdash_v1.0.py`) for analyzing compressed-air
system CSV logs — current, pressure, calculated power/flow trends, KPIs (duty factor, load
factor, specific efficiency), and a Power-vs-Pressure density plot.

---

## What you need

Only **two files** are required to run the app:

- `cdash_v1.0.py` — the application
- `requirements.txt` — the pinned dependencies

No CSV data is bundled — you upload your logs through the dashboard's **Section 1** after it starts.

**Python:** 3.13.x (the pinned wheels in `requirements.txt` are built for Python 3.13).

---

## Setup & run

Pick the instructions for your operating system / terminal. Steps 4–6 (create venv + install)
are a **one-time** setup; after that, running again only needs steps 3, 5, and 7.

> **`<your folder name>`** in the commands below is the folder where you saved the downloaded
> files (`cdash_v1.0.py` and `requirements.txt`). Replace it with that folder's actual name/path.
> For example, if you saved them in `C:\Users\you\Downloads\CDASH`, then `<your folder name>` is
> `Downloads\CDASH`. Also replace `<you>` with your Windows username; do NOT type the `< >` brackets.

### Windows — PowerShell

```powershell
# 1. Install Python 3.13.x (one-time):
#    https://www.python.org/downloads/  —  tick "Add python.exe to PATH" on the first screen,
#    then close and reopen the terminal.

# 2. Confirm Python (should print 3.13.x)
python --version

# 3. cd into the folder holding cdash_v1.0.py and requirements.txt
#    (replace <you> with your Windows username if different; do NOT type < > brackets)
cd "C:\Users\<you>\<your folder name>"

# 4. Create a virtual environment
python -m venv venv

# 5. Activate it  (prompt then shows (venv) at the start of the line)
.\venv\Scripts\Activate.ps1
#    If activation is blocked by an execution-policy error, run this ONCE then retry step 5:
#    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 6. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 7. Run
python cdash_v1.0.py
```

### Windows — Command Prompt (cmd.exe)

```bat
REM 1. Install Python 3.13.x (one-time):
REM    https://www.python.org/downloads/  —  tick "Add python.exe to PATH", then reopen Command Prompt.

REM 2. Confirm Python (should print 3.13.x)
python --version

REM 3. cd into the folder holding cdash_v1.0.py and requirements.txt
REM    (replace <you> with your Windows username if different; do NOT type < > brackets)
cd "C:\Users\<you>\<your folder name>"

REM 4. Create a virtual environment
python -m venv venv

REM 5. Activate it  (prompt then shows (venv))
venv\Scripts\activate.bat

REM 6. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

REM 7. Run
python cdash_v1.0.py
```

### macOS (Terminal — zsh/bash)

```bash
# 1. Install Python 3.13.x (one-time), e.g. with Homebrew:
#    brew install python@3.13

# 2. Confirm Python
python3 --version

# 3. cd into the folder holding cdash_v1.0.py and requirements.txt
cd ~/<your folder name>

# 4. Create a virtual environment
python3 -m venv venv

# 5. Activate it  (prompt then shows (venv))
source venv/bin/activate

# 6. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 7. Run
python cdash_v1.0.py
```

---

## Running it again (after the first time)

Steps 1, 4, and 6 (install Python, create the venv, install dependencies) are **one-time
setup**. Every time after that, you only need **3 steps**: open a terminal and repeat
**step 3 → step 5 → step 7** — cd into the folder, activate the venv, run the app.

**Windows — PowerShell**
```powershell
cd "C:\Users\<you>\<your folder name>"
.\venv\Scripts\Activate.ps1
python cdash_v1.0.py
```

**Windows — Command Prompt (cmd.exe)**
```bat
cd "C:\Users\<you>\<your folder name>"
venv\Scripts\activate.bat
python cdash_v1.0.py
```

**macOS (Terminal)**
```bash
cd ~/<your folder name>
source venv/bin/activate
python cdash_v1.0.py
```

> Only re-run the one-time steps if you move/rebuild the venv, switch to a new
> computer, or `requirements.txt` changes (then re-run step 6: `pip install -r requirements.txt`).

---

## Open the dashboard

Once running, open a browser to:

**http://127.0.0.1:8050**

To stop the server, press **Ctrl + C** in the terminal.
To leave the virtual environment, type **`deactivate`**.

---

## Notes & troubleshooting

- **`(venv)` must be visible** in your prompt before running `pip install` / `python cdash_v1.0.py`,
  otherwise packages install into the system Python instead of the isolated environment.
- **Auto-reload / debug:** the app runs with `debug=True`, so it reloads on code edits and shows
  errors in the browser. For sharing or production, set `debug=False` on the last line.
- **Port 8050 already in use:** another Dash app is likely running. Stop it, or change the last
  line to `app.run(debug=True, port=8060)` and browse to that port.
- **Access from another device on the same network:** change the run line to
  `app.run(debug=True, host="0.0.0.0")`, then browse to `http://<this-computer-IP>:8050`.
- **Large / multiple uploads:** the request-size cap is set to 1 GB in the app. Individual files
  are capped at 100 MB each (client-side). Adjust in `cdash_v1.0.py` if needed.

---

## Dependencies (pinned)

```
dash==2.16.1
plotly==5.20.0
pandas==3.0.2
numpy==2.3.3
matplotlib==3.10.9
scipy==1.17.1
```
