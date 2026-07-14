# =============================================================================
# Compressed-Air Diagnotics, Analytics & Statistics HUb  (cdash_v1.0.py)
# =============================================================================
# A single-file Dash (Plotly) web app for analyzing compressed-air system logs.
# Developed by Texas A&M University Gulf Coast Center of Excellence (GCCOE) &
# Industrial Training & Assesment Center (ITAC)
#
# WHAT IT DOES
#   1. User uploads one or more CSV logs (current-draw logs and/or pressure logs).
#   2. For "current" logs, given a few motor specs, it estimates electrical Power
#      and compressed-air Flow, plus KPIs (duty factor, load factor, specific
#      efficiency, energy).
#   3. It draws interactive time-series charts, a raw-data preview, a KPI table,
#      and a Power-vs-Pressure density plot.
#
# HOW THE PIECES FIT TOGETHER (data flow)
#   upload  ->  process_uploaded_files()  ->  fills the global `trend_dfs` dict
#               (one entry per file: raw dataframe + detected column names + type)
#   select files in dropdown  ->  update_raw_data()   draws Section 2 preview
#                              ->  render_inputs()     draws the per-file spec boxes
#   click "Apply" (or zoom a chart)  ->  update_plot()  does all the heavy math and
#               returns the time-series figure, the Section 5 image, and the KPI table.
#
# KEY IDEAS FOR SOMEONE EDITING THIS FILE
#   * `trend_dfs` is a module-level (global) cache. Processed results are stored back
#     into it under "df_processed" so that zooming a chart doesn't recompute everything.
#   * A "current" file only produces Power/Flow/KPIs/Section-5 when ALL FOUR specs
#     (FLA, HP, Rated Flow, Control Scheme) are provided -- see inputs_complete().
#   * The UI is defined once in `app.layout`; the @app.callback functions below react
#     to user actions and return new content into the layout's `id=...` placeholders.
#
# COMMON CHANGES & WHERE TO MAKE THEM
#   * Chart/curve colors ............... inside update_plot(), the go.Scattergl(... line=dict(color=...))
#   * Which CSV columns are recognized . pick_current_column / pick_datetime_column / pick_pressure_column
#   * Moving-average behavior .......... zero_phase_ma_filter() + the `global-ma` / `ma-overlay` inputs
#   * Max upload size .................. app.server.config['MAX_CONTENT_LENGTH'] and dcc.Upload(max_size=...)
#   * Server port / debug ............. the app.run(...) call at the very bottom
#   * Power / Flow model math ......... get_power_factor_equation() and from_performance_curve_equation()
# =============================================================================

# ---- Standard library ----
import base64          # decode the base64 payload that dcc.Upload sends
import io              # wrap decoded bytes as an in-memory file for pandas / image buffers

# ---- Dash: the web framework (layout components + callback wiring) ----
from dash import Dash, dcc, html, Input, Output, State, MATCH, ALL, dash_table, ctx, no_update

# ---- Plotly: the interactive time-series charts ----
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# ---- Numerical / data libraries ----
import numpy as np
import math
import pandas as pd

from scipy.signal import filtfilt  # zero-phase (no lag) moving-average filtering

# --- Matplotlib for Static High-Density Scatter Plots & Histograms ---
# Matplotlib renders Section 5 and the histograms to PNG images (server-side),
# which are then embedded in the page as base64 data URIs.
import matplotlib
matplotlib.use('Agg')              # 'Agg' = headless backend; required so it works without a display
import matplotlib.pyplot as plt
import matplotlib.colors as mc
import colorsys
from matplotlib.ticker import MultipleLocator

# Global dictionary to store uploaded data and CACHED processed data.
# Structure per file:  trend_dfs[filename] = {
#     "df": <raw DataFrame>, "xaxis": <time col>, "yaxis": <value col>,
#     "type": "current" | "pressure",
#     # added later by update_plot():
#     "df_processed": <DataFrame with Power/Flow/MA columns>, "complete": <bool> }
# NOTE: because this is a module global, data persists across callbacks but is shared
# by all browser sessions and is lost when the server restarts.
trend_dfs = {}


# Return a darker shade of `color` (used to draw the moving-average line darker than
# the raw line of the same series). `amount` < 1 darkens; the try/except keeps it safe
# for any color string matplotlib understands.
def darken_color(color, amount=0.6):
    try:
        c = mc.to_rgb(color)
        hls = colorsys.rgb_to_hls(*c)
        dark_rgb = colorsys.hls_to_rgb(hls[0], max(0, hls[1] * amount), hls[2])
        return mc.to_hex(dark_rgb)
    except:
        return color


# --- Column auto-detection helpers ---------------------------------------------
# Each looks through the CSV's column names for a keyword and returns the first match
# (case-insensitive). Add/'reorder keywords here to support differently-named columns.

# Find the current (Amps) column.
def pick_current_column(df):
    for unit in ["amp", "Amp", "AMP", "current", "Current"]:
        matching_cols = [col for col in df.columns if unit in col.lower()]
        if matching_cols: return matching_cols[0]
    return None

# Find the timestamp column (used as the x-axis).
def pick_datetime_column(df):
    for unit in ["cdt", "cst", "time", "date"]:
        matching_cols = [col for col in df.columns if unit in col.lower()]
        if matching_cols: return matching_cols[0]
    return None

# Find the pressure column.
def pick_pressure_column(df):
    for unit in ["psi", "kpa", "press"]:
        matching_cols = [col for col in df.columns if unit in col.lower()]
        if matching_cols: return matching_cols[0]
    return None


# Return a function that maps a fraction-of-full-load-amps value to motor power factor.
# The polynomial depends on motor size (HP) because small and large motors behave
# differently. `np.maximum(0.2, ...)` clamps the power factor to a sensible floor.
# These curves are empirical fits -- edit the coefficients only if you have better data.
def get_power_factor_equation(hp):
    hp = float(hp)
    if hp < 20: return lambda fla: np.maximum(0.2, -13.888*(fla**4) + 44.278*(fla**3) - 52.764*(fla**2) + 27.879*fla - 4.815)
    elif 20 <= hp < 50: return lambda fla: np.maximum(0.2, -14.924*(fla**4) + 47.039*(fla**3) - 55.508*(fla**2) + 29.126*fla - 4.985)
    elif 50 <= hp < 100: return lambda fla: np.maximum(0.2, -15.461*(fla**4) + 48.93*(fla**3) - 57.989*(fla**2) + 30.55*fla - 5.238)
    elif 100 <= hp < 150: return lambda fla: np.maximum(0.2, -14.161*(fla**4) + 45.438*(fla**3) - 54.65*(fla**2) + 29.24*fla - 5.047)
    elif 150 <= hp < 200: return lambda fla: np.maximum(0.2, -15.656*(fla**4) + 49.769*(fla**3) - 59.257*(fla**2) + 31.373*fla - 5.382)
    else: return lambda fla: np.maximum(0.2, -15.669*(fla**4) + 50.242*(fla**3) - 60.291*(fla**2) + 32.132*fla - 5.531)


# Return a function that maps % of rated power -> % of rated flow (air delivered),
# based on the compressor's control scheme (how it modulates output):
#   1 = Load/Unload, 2 = Inlet Valve Modulation, 3 = Variable Displacement, 4 = Variable Speed Drive.
# Each scheme has a distinct part-load curve. These map to the Control Scheme dropdown values.
def from_performance_curve_equation(category):
    if category == 1: return lambda percent_power: np.where(percent_power < 95, 0, 100)
    elif category == 2: return lambda percent_power: np.select([percent_power < 60, (percent_power >= 60) & (percent_power < 80), percent_power >= 80], [0, np.maximum(0, 0.746 * percent_power - 19.4), np.minimum(100, 3.414 * percent_power - 241.67)], default=0)
    elif category == 3: return lambda percent_power: np.select([percent_power < 45, (percent_power >= 45) & (percent_power < 60), percent_power >= 60], [0, np.maximum(0, 1.11 * percent_power - 28), np.minimum(100, 1.48 * percent_power - 45.55)], default=0)
    elif category == 4: return lambda percent_power: np.maximum(0, 1.03 * percent_power - 2.567)
    else: return None


# Build the light-red weekend shading rectangles for the time-series charts.
# It finds every Saturday, then shades a 2-day span (Sat + Sun). `yref="paper"` makes
# each rectangle span the full chart height regardless of the data's y-range.
def find_shapes(df, xaxis):
    saturdays = df[df[xaxis].dt.weekday == 5][xaxis].dt.normalize().unique()
    shapes = []
    for sat in saturdays:
        start = pd.Timestamp(sat)
        end = min(start + pd.Timedelta(days=2), df[xaxis].max())
        shapes.append(dict(type="rect", xref="x", yref="paper", x0=start, x1=end, y0=0, y1=1, fillcolor="rgba(255,204,204,0.35)", layer="below", line_width=0))
    return shapes


# Zero-Phase Moving Average Filter execution using Scipy.
# A normal moving average shifts (lags) the smoothed curve; filtfilt runs the filter
# forward AND backward so the result stays aligned in time (no lag). `window_samples`
# is the smoothing window measured in data points (converted from minutes by the caller).
def zero_phase_ma_filter(series, window_samples):
    filled = series.interpolate(method='linear').bfill().ffill().to_numpy()   # fill gaps so the filter has no NaNs
    if len(filled) <= 3 or window_samples < 2:
        return filled   # too little data (or trivial window) to filter meaningfully

    # Ensures window isn't larger than the data
    w = min(window_samples, len(filled))
    b = np.ones(w) / w   # a flat (boxcar) averaging kernel of length w
    a = 1

    padlen = min(3 * w, len(filled) - 1)   # edge padding; capped so it never exceeds the data length
    return filtfilt(b, a, filled, padlen=padlen)


# ADVANCED LTTB Downsampler.
# Plotting millions of points is slow; LTTB reduces a series to ~max_points while
# preserving its visual shape (peaks/dips), which a naive every-Nth-point sample loses.
# It returns the subset of ORIGINAL rows nearest the chosen points, so other columns
# (Power, Flow, ...) stay aligned. Raise/lower max_points to trade detail vs. speed.
# Vendored LTTB (Largest-Triangle-Three-Buckets) index selector.
# Replaces the external `lttb` package (which is unmaintained and caps numpy < 2).
# Given monotonic-x series (x, y), returns the indices of `n_out` points that best
# preserve the visual shape: the first and last points are always kept, the middle
# points are split into equal buckets, and from each bucket the point forming the
# largest triangle (with the previously kept point and the next bucket's average) is
# chosen. This matches the classic Steinarsson algorithm the `lttb` package implements.
def _lttb_downsample_indices(x, y, n_out):
    n = len(x)
    if n_out >= n or n_out < 3:
        return np.arange(n)

    n_bins = n_out - 2
    # Split the middle points (indices 1 .. n-2) into n_bins buckets, exactly as the
    # reference does with np.array_split (earlier buckets absorb any remainder).
    bins = np.array_split(np.arange(1, n - 1), n_bins)

    idx = np.empty(n_out, dtype=int)
    idx[0] = 0            # always keep the first point
    idx[-1] = n - 1       # always keep the last point

    a = 0   # index of the point kept in the PREVIOUS bucket (triangle's first vertex)
    for i in range(n_bins):
        this_bin = bins[i]
        # Far vertex = centroid of the NEXT bucket (or just the final point for the last bucket).
        if i < n_bins - 1:
            cx, cy = x[bins[i + 1]].mean(), y[bins[i + 1]].mean()
        else:
            cx, cy = x[n - 1], y[n - 1]
        ax, ay = x[a], y[a]
        bx, by = x[this_bin], y[this_bin]
        # Triangle area for each candidate (the 0.5 factor is omitted -- it doesn't affect argmax).
        areas = np.abs((ax - cx) * (by - ay) - (ax - bx) * (cy - ay))
        a = int(this_bin[np.argmax(areas)])
        idx[i + 1] = a
    return idx


def downsample_df_lttb(df, x_col, y_col, max_points=10000):
    if len(df) <= max_points: return df.copy()   # already small enough

    df_clean = df.dropna(subset=[x_col, y_col])
    if len(df_clean) <= max_points: return df_clean.copy()

    x_numeric = pd.to_numeric(df_clean[x_col]).to_numpy()   # LTTB needs numeric x (datetimes -> int64 ns)
    y_numeric = pd.to_numeric(df_clean[y_col]).to_numpy()

    idx = _lttb_downsample_indices(x_numeric, y_numeric, max_points)   # indices of the kept points
    return df_clean.iloc[idx].copy()


# Power/Flow/KPIs (Duty & Load factor, efficiency) and the Section 5 plot require
# all four compressor specs. Returns False if any is missing or non-positive/unset.
# This is the single gate that decides whether a "current" file is fully analyzed.
def inputs_complete(fla, hp, rf, cs):
    try:
        return (fla is not None and float(fla) > 0 and
                hp is not None and float(hp) > 0 and
                rf is not None and float(rf) > 0 and
                cs in (1, 2, 3, 4))
    except (TypeError, ValueError):
        return False


# =============================================================================
# APP SETUP
# =============================================================================
app = Dash(__name__)
# Max size (bytes) of the WHOLE upload request. dcc.Upload base64-encodes files
# (~1.37x the raw size), so keep this well above the combined raw size of a batch
# to avoid "413 Request Entity Too Large". 1024**3 = 1 GB.
app.server.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024

# =============================================================================
# PAGE LAYOUT
# =============================================================================
# The static structure of the page. Each `id=...` is a placeholder that a callback
# below fills in with dynamic content. Styling is inline Python dicts (CSS in dict form).
app.layout = html.Div([
    html.H1("Compressed-air Diagnostics Analytics & Statistics Hub (C-DASH)", style={'textAlign': 'center', 'fontFamily': 'sans-serif', 'color': '#2c3e50'}),

    # ---- Section 1: Upload & Configure -------------------------------------
    html.Div([
        html.H3("1. Upload & Configure Data", style={'marginTop': '0', 'color': '#34495e'}),
        # File upload box. multiple=True allows selecting several CSVs at once.
        dcc.Upload(
            id='upload-data', children=html.Div(['Upload ', html.A('CSV File(s)')]),
            style={'width': '100%', 'height': '60px', 'lineHeight': '60px', 'borderWidth': '2px', 'borderStyle': 'dashed', 'borderColor': '#bdc3c7', 'borderRadius': '5px', 'textAlign': 'center', 'marginBottom': '15px', 'background': '#f8f9fa', 'cursor': 'pointer'},
            multiple=True, max_size=150857600   # per-file client-side cap (~150 MB raw)
        ),
        # Dropdown listing uploaded files; multi=True lets the user analyze several together.
        dcc.Dropdown(id="trend-selector", options=[], value=[], multi=True, style={"width": "100%", "marginBottom": "15px"}, placeholder="Upload files above to populate this list..."),
        # Global settings row: line voltage + kPa->psig toggle
        html.Div([
            html.Label("Operating Voltage (V):", style={'fontWeight': 'bold', 'marginRight': '8px'}),
            dcc.Input(id='voltage', type='number', value=None, style={'marginRight': '30px', 'padding': '4px'}),
            dcc.Checklist(id='pressure-amplify', options=[{'label': ' Convert pressure to psig ', 'value': 'amplify'}], value=[], style={'fontWeight':'bold', 'display': 'inline-block'})
        ], style={'marginBottom': '15px'}),
        # Moving-average controls: a checkbox to overlay the MA curve + the window in minutes.
        html.Div([
            dcc.Checklist(id='ma-overlay', options=[{'label': ' Overlay moving average curve  ', 'value': 'show'}], value=[], style={'fontWeight': 'bold', 'display': 'inline-block', 'marginRight': '20px'}),
            html.Label("Moving Avg (mins):", style={'marginRight': '6px', 'color': "#000000", 'fontWeight': 'bold'}),
            dcc.Input(id='global-ma', type='number', value=None, style={'width': '70px', 'padding': '4px'})
        ], style={'marginBottom': '15px'}),
        # Placeholder filled by render_inputs() with one spec row per selected current file.
        html.Div(id='per-file-inputs', style={'padding': '10px', 'background': '#f1f2f6', 'borderRadius': '8px'}),
        # The button that triggers the main recompute (update_plot).
        html.Button("Apply Changes & Update Plots", id="apply-btn", n_clicks=0, style={'backgroundColor': '#27ae60', 'color': 'white', 'padding': '12px 20px', 'border': 'none', 'borderRadius': '5px', 'fontSize': '16px', 'cursor': 'pointer', 'marginTop': '20px', 'fontWeight': 'bold', 'width': '100%'})
    ], style={'padding': '20px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)', 'borderRadius': '8px', 'marginBottom': '20px', 'background': 'white'}),

    # ---- Section 2: Raw data preview (filled by update_raw_data) -----------
    html.Div([html.H3("2. Raw Data Overview & Statistics", style={'marginTop': '0', 'color': '#34495e'}), html.Div(id='raw-data-container')], style={'padding': '20px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)', 'borderRadius': '8px', 'marginBottom': '20px', 'background': 'white'}),
    # ---- Section 3: KPI table (filled by update_plot -> 'stats-row') --------
    html.Div([
        html.H3("3. Key Performance Indicators", style={'marginTop': '0', 'color': '#34495e'}),
        html.Div(id='stats-row')
    ], style={'padding': '20px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)', 'borderRadius': '8px', 'marginBottom': '20px', 'background': 'white'}),
    # ---- Section 4: Interactive time-series charts (filled by update_plot) --
    html.Div([html.H3("4. Time Series Trends", style={'marginTop': '0', 'color': '#34495e'}), dcc.Graph(id="timeseries-plot"), html.Div("Note: The light red background highlights weekends.", style={'fontSize': '0.85em', 'color': '#7f8c8d', 'fontStyle': 'italic', 'marginTop': '8px'})], style={'padding': '20px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)', 'borderRadius': '8px', 'marginBottom': '20px', 'background': 'white'}),
    # ---- Section 5: Power-vs-Pressure density image (filled by update_plot) -
    html.Div([html.H3("5. Power vs Pressure Analysis (Density Grid)", style={'marginTop': '0', 'color': '#34495e'}), html.Div(id="scatter-plot-container", style={"display": "flex", "justifyContent": "center", "width": "100%", "overflowX": "auto"})], style={'padding': '20px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.1)', 'borderRadius': '8px', 'marginBottom': '20px', 'background': 'white'}),
    html.Div("Developed by Texas A&M University Gulf Coast Center of Excellence (GCCoE) and Industrial Training & Assessment Center (ITAC)", style={'textAlign': 'right', 'fontSize': '0.8em', 'color': '#7f8c8d', 'fontStyle': 'italic', 'marginTop': '10px', 'paddingRight': '5px'})
], style={'padding': '20px', 'backgroundColor': '#ecf0f1', 'fontFamily': 'sans-serif'})


# =============================================================================
# CALLBACK 1: handle file uploads
# =============================================================================
# Runs whenever new files are dropped on the upload box. Parses each CSV, detects its
# columns and type, stores it in `trend_dfs`, and refreshes the dropdown options.
@app.callback(
    Output('trend-selector', 'options'), Output('trend-selector', 'value'),
    Input('upload-data', 'contents'), State('upload-data', 'filename'), State('trend-selector', 'value')
)
def process_uploaded_files(contents, filenames, current_selection):
    current_selection = current_selection or []
    new_files_added = []
    if contents:
        for content, filename in zip(contents, filenames):
            if filename not in trend_dfs:   # skip files already loaded (dedupe by name)
                _, content_string = content.split(',')          # strip the "data:...;base64," prefix
                decoded = base64.b64decode(content_string)
                try:
                    df = pd.read_csv(io.StringIO(decoded.decode('utf-8')))
                    # Detect which columns to use and whether this is a pressure or current log.
                    xaxis = pick_datetime_column(df)
                    yaxis_press = pick_pressure_column(df)
                    yaxis_curr = pick_current_column(df)
                    if yaxis_press: yaxis, trend_type = yaxis_press, "pressure"
                    elif yaxis_curr: yaxis, trend_type = yaxis_curr, "current"
                    else: continue   # neither pressure nor current column found -> skip this file
                    # Coerce types, drop timezone, remove bad rows, sort by time.
                    df[yaxis] = pd.to_numeric(df[yaxis], errors='coerce')
                    df[xaxis] = pd.to_datetime(df[xaxis], errors='coerce')
                    if df[xaxis].dt.tz is not None: df[xaxis] = df[xaxis].dt.tz_localize(None)
                    df = df.dropna(subset=[xaxis, yaxis]).copy()
                    df = df.sort_values(by=xaxis).reset_index(drop=True)
                    trend_dfs[filename] = {"df": df, "xaxis": xaxis, "yaxis": yaxis, "type": trend_type}
                    new_files_added.append(filename)
                except Exception as e: print(f"Error processing {filename}: {e}")   # errors only print to the server console
    # Rebuild dropdown options; pressure files get a "[Pressure]" tag in their label.
    press_files = [f for f in trend_dfs if trend_dfs[f]['type'] == 'pressure']
    all_options = [{"label": (f.split('.')[0] + (" [Pressure]" if f in press_files else "")), "value": f} for f in trend_dfs]
    # Auto-select any newly added files (append to whatever was already selected).
    return all_options, current_selection + new_files_added


# =============================================================================
# CALLBACK 2: Section 2 raw-data preview
# =============================================================================
# For each selected file, builds a tab showing the first/last rows, a stats table,
# and (for current logs) a histogram image of the current distribution.
@app.callback(Output('raw-data-container', 'children'), Input('trend-selector', 'value'))
def update_raw_data(selected_files):
    if not selected_files: return html.Div("Select files from the dropdown above to view raw data.", style={'color': 'gray', 'fontStyle': 'italic'})
    tabs = []
    for fname in selected_files:
        if fname not in trend_dfs: continue
        df, xaxis, yaxis, trend_type = trend_dfs[fname]['df'], trend_dfs[fname]['xaxis'], trend_dfs[fname]['yaxis'], trend_dfs[fname]['type']
        # Build a compact preview: first 3 rows, an "..." separator, last 3 rows.
        if len(df) > 6:
            head_df, tail_df = df.head(3).copy(), df.tail(3).copy()
            head_df[xaxis], tail_df[xaxis] = head_df[xaxis].dt.strftime('%Y-%m-%d %H:%M:%S'), tail_df[xaxis].dt.strftime('%Y-%m-%d %H:%M:%S')
            display_df = pd.concat([head_df, pd.DataFrame([{col: "..." for col in df.columns}]), tail_df])
        else:
            display_df = df.copy()
            display_df[xaxis] = display_df[xaxis].dt.strftime('%Y-%m-%d %H:%M:%S')
        # Summary statistics (count/mean/std/min/quartiles/max) of the value column.
        stats_df = df[[yaxis]].describe().reset_index().rename(columns={'index': 'Statistic', yaxis: 'Value'})
        stats_df['Value'] = stats_df['Value'].round(2)

        # For current logs, render a distribution histogram to a PNG and embed it.
        hist_div = html.Div()
        if trend_type == 'current':
            fig_hist, ax_hist = plt.subplots(figsize=(10, 4))
            ax_hist.hist(df[yaxis].dropna(), bins=200, color='#3498db', edgecolor='black', linewidth=0.3, alpha=0.8)
            ax_hist.set_title(f"Current Distribution Histogram (Amps)", fontsize=14, pad=10)
            ax_hist.set_xlabel("Current (Amps)")
            ax_hist.set_ylabel("Frequency")
            ax_hist.grid(axis='y', linestyle='--', alpha=0.7)
            plt.tight_layout()
            buf_hist = io.BytesIO()
            fig_hist.savefig(buf_hist, format="png", dpi=100)
            plt.close(fig_hist)   # always close matplotlib figures to free memory
            b64_hist = base64.b64encode(buf_hist.getbuffer()).decode("utf8")
            hist_div = html.Div([html.Img(src=f"data:image/png;base64,{b64_hist}", style={"maxWidth": "100%", "height": "auto", "borderRadius": "5px"})], style={'marginTop': '20px', 'borderTop': '1px solid #d6d6d6', 'paddingTop': '20px', 'textAlign': 'center'})

        # Assemble one tab: preview table (left) + stats table (right), histogram below.
        tab_content = html.Div([
            html.Div([
                html.Div([
                    html.Strong("Preview (First & Last Rows)", style={'marginBottom': '10px', 'display': 'block'}),
                    dash_table.DataTable(data=display_df.to_dict('records'), columns=[{"name": i, "id": i} for i in display_df.columns], style_header={'backgroundColor': '#ecf0f1', 'fontWeight': 'bold', 'textAlign': 'center'}, style_cell={'textAlign': 'center', 'padding': '5px', 'fontFamily': 'sans-serif'}, style_table={'border': '1px solid #ccc', 'borderRadius': '4px'})
                ], style={'width': '65%', 'display': 'inline-block', 'verticalAlign': 'top', 'paddingRight': '20px'}),
                html.Div([
                    html.Strong(f"'{yaxis}' Statistics", style={'marginBottom': '10px', 'display': 'block'}),
                    dash_table.DataTable(data=stats_df.to_dict('records'), columns=[{"name": i, "id": i} for i in stats_df.columns], style_header={'backgroundColor': '#ecf0f1', 'fontWeight': 'bold', 'textAlign': 'center'}, style_cell={'textAlign': 'center', 'padding': '5px', 'fontFamily': 'sans-serif'}, style_table={'border': '1px solid #ccc', 'borderRadius': '4px'})
                ], style={'width': '30%', 'display': 'inline-block', 'verticalAlign': 'top'})
            ], style={'display': 'flex', 'flexDirection': 'row', 'justifyContent': 'space-between'}),
            hist_div
        ], style={'padding': '20px', 'backgroundColor': '#fff', 'border': '1px solid #d6d6d6', 'borderTop': 'none'})
        tabs.append(dcc.Tab(label=fname, value=fname, children=[tab_content], style={'fontWeight': 'bold'}))
    return dcc.Tabs(id="raw-data-tabs", value=selected_files[0], children=tabs)


# =============================================================================
# CALLBACK 3: per-file spec inputs (Section 1, lower area)
# =============================================================================
# Rebuilds the spec entry boxes when the selection changes. The tricky part: it reads
# the CURRENT values of the existing (dynamic) inputs so the user's typed numbers are
# preserved across a rebuild -- that's what the State({'type':...,'index':ALL}) do, and
# the prev_* dicts map filename -> last value.
@app.callback(
    Output('per-file-inputs', 'children'), Input('trend-selector', 'value'),
    State({'type':'fla','index':ALL}, 'value'), State({'type':'fla','index':ALL}, 'id'),
    State({'type':'hp','index':ALL}, 'value'), State({'type':'hp','index':ALL}, 'id'),
    State({'type':'rf','index':ALL}, 'value'), State({'type':'rf','index':ALL}, 'id'),
    State({'type':'cs','index':ALL}, 'value'), State({'type':'cs','index':ALL}, 'id')
)
def render_inputs(selected_files, fla_vals, fla_ids, hp_vals, hp_ids, rf_vals, rf_ids, cs_vals, cs_ids):
    if not selected_files: return html.Div("No files selected.", style={'color': 'gray', 'fontStyle': 'italic'})
    # Map each existing input's file index -> its current value, to repopulate after rebuild.
    prev_fla = {item['index']: val for item, val in zip(fla_ids, fla_vals)} if fla_ids else {}
    prev_hp = {item['index']: val for item, val in zip(hp_ids, hp_vals)} if hp_ids else {}
    prev_rf = {item['index']: val for item, val in zip(rf_ids, rf_vals)} if rf_ids else {}
    prev_cs = {item['index']: val for item, val in zip(cs_ids, cs_vals)} if cs_ids else {}

    # Collapsible help explaining how to estimate FLA (Full Load Amps).
    help_section = html.Details([
        html.Summary("ℹ️ How to estimate Full Load Amps (FLA)? (Click to expand)", style={'cursor': 'pointer', 'color': '#2980b9', 'fontWeight': 'bold', 'fontSize': '0.9em', 'marginBottom': '10px'}),
        html.Div([
            html.Div([html.B("Option 1 (Preferred): "), "Estimate from CAGI datasheet if available. FLA = (Power (kW) x 1000)/(0.85 x 1.73 x Voltage)"]),
            html.Div([html.B("Option 2: "), "Get from peak AC current consumption profile. Note: Do not select from instant current pike occuring when motor starts."]),
            html.Div([html.B("Option 3: "), "Obtain from MEASUR calculator,", html.A("https://measur.ornl.gov/calculators/full-load-amps", href="https://measur.ornl.gov/calculators/full-load-amps",target="_blank"), " (add 10% to the value obtained from this calculator to account for auxillary loads)."])
        ], style={'fontSize': '0.9em', 'background': '#e8f4f8', 'padding': '12px', 'borderRadius': '5px', 'marginBottom': '15px'})
    ])
    # Reminder that the four specs are optional but required for the advanced outputs.
    inputs_note = html.Div([
        html.B("Note: "),
        "FLA, HP, Rated Flow and Control Scheme are required to compute Power, Flow, the KPIs (Duty Factor, Load Factor, Specific Efficiency) and the Section 5 Power-vs-Pressure plot. ",
        "Leave any of them blank to skip these and just explore the Current / Pressure trends."
    ], style={'fontSize': '0.9em', 'background': '#fef5e7', 'border': '1px solid #f5cba7', 'padding': '10px 12px', 'borderRadius': '5px', 'marginBottom': '15px'})
    rows = [help_section, inputs_note]
    # One spec row per CURRENT file. Pressure files need no specs, so none is shown.
    # Each dcc.Input uses a "pattern-matching" id {'type': 'fla', 'index': <filename>} so
    # update_plot() can collect them all with State({'type':'fla','index':ALL}).
    for fname in selected_files:
        base = fname.split('.')[0]
        if trend_dfs[fname]['type'] == 'current':
            rows.append(
                html.Div([
                    html.Strong(f"{base} Specs: ", style={'marginRight':'15px', 'display': 'inline-block', 'width': '180px'}),
                    html.Label("FLA:", style={'marginRight':'6px'}), dcc.Input(id={'type':'fla','index':fname}, type='number', value=prev_fla.get(fname, None), style={'marginRight':'16px','width':'70px'}),
                    html.Label("HP:", style={'marginRight':'6px'}), dcc.Input(id={'type':'hp','index':fname}, type='number', value=prev_hp.get(fname, None), style={'marginRight':'16px','width':'70px'}),
                    html.Label("Rated Flow (acfm):", style={'marginRight':'6px'}), dcc.Input(id={'type':'rf','index':fname}, type='number', value=prev_rf.get(fname, None), style={'marginRight':'16px','width':'70px'}),
                    html.Label("Control Scheme:", style={'marginRight':'6px'}),
                    dcc.Dropdown(id={'type':'cs','index':fname}, options=[{'label': 'Load/Unload', 'value': 1}, {'label': 'Inlet Valve Modulation', 'value': 2}, {'label': 'Variable Displacement', 'value': 3}, {'label': 'Variable Speed Drive', 'value': 4}], value=prev_cs.get(fname, None), style={'width':'220px', 'display':'inline-block', 'verticalAlign': 'middle'})
                ], style={"marginBottom":"10px", "borderBottom": "1px solid #ccc", "paddingBottom": "8px"})
            )
    return html.Div(rows)


# =============================================================================
# CALLBACK 4: the main engine -- builds charts, Section 5, and the KPI table
# =============================================================================
# Triggered by (a) clicking "Apply" or (b) zooming/panning the time-series chart
# (relayoutData). On "Apply" it recomputes everything; on zoom it reuses cached
# processed data and just recomputes KPIs for the visible window.
@app.callback(
    Output("timeseries-plot", "figure"), Output("scatter-plot-container", "children"), Output("stats-row", "children"),
    Input("apply-btn", "n_clicks"), Input("timeseries-plot", "relayoutData"),
    State("voltage", "value"), State("trend-selector", "value"),
    State({'type':'fla','index':ALL}, 'value'), State({'type':'fla','index':ALL}, 'id'),
    State({'type':'hp','index':ALL}, 'value'), State({'type':'hp','index':ALL}, 'id'),
    State({'type':'rf','index':ALL}, 'value'), State({'type':'rf','index':ALL}, 'id'),
    State({'type':'cs','index':ALL}, 'value'), State({'type':'cs','index':ALL}, 'id'),
    State("global-ma", "value"), State("ma-overlay", "value"),
    State("pressure-amplify", "value")
)
def update_plot(n_clicks, relayout_data, V, selected_files, fla_vals, fla_ids, hp_vals, hp_ids, rf_vals, rf_ids, cs_vals, cs_ids, global_ma, ma_overlay, amplify_opt):
    empty_kpi = html.Div("Click 'Apply Changes' to process data...", style={'color': 'gray', 'fontStyle': 'italic'})
    V = V or 460   # default line voltage if the field is blank
    # Nothing to do if no files are selected, or on the very first load with no interaction.
    if not selected_files or (n_clicks == 0 and not relayout_data): return go.Figure(), html.Div(), empty_kpi

    # If the user zoomed, capture the visible x (time) range so KPIs reflect just that window.
    zoom_xrange = None
    if relayout_data and 'xaxis.range[0]' in relayout_data:
        zoom_xrange = (pd.to_datetime(relayout_data["xaxis.range[0]"]), pd.to_datetime(relayout_data["xaxis.range[1]"]))

    # Figure out WHY this callback fired. "apply-btn" (or first run) => full recompute;
    # a chart interaction => reuse cached processed data.
    trigger = ctx.triggered_id
    needs_recalc = (trigger == "apply-btn" or trigger is None)

    # Collect the per-file spec inputs into {filename: value} lookup maps.
    fla_map = {item['index']: val for item, val in zip(fla_ids, fla_vals)} if fla_ids else {}
    hp_map = {item['index']: val for item, val in zip(hp_ids, hp_vals)} if hp_ids else {}
    rf_map = {item['index']: val for item, val in zip(rf_ids, rf_vals)} if rf_ids else {}
    cs_map = {item['index']: val for item, val in zip(cs_ids, cs_vals)} if cs_ids else {}
    ma_val = max(0.1, float(global_ma or 5))     # moving-average window in minutes (default 5)
    show_ma = 'show' in (ma_overlay or [])        # whether to draw the MA overlay curves
    amplify_pressure = 'amplify' in (amplify_opt or [])   # whether to multiply pressure by 6.89 (kPa->psi)

    # Decide figure structure up front. The Power & Flow rows only exist if at least
    # one selected current file has complete specs. On recalc that is the live input
    # state; on zoom we reuse the completeness cached from the last Apply so the figure
    # matches the processed data (which only has Power/Flow columns when complete).
    def file_is_complete(f):
        e = trend_dfs.get(f, {})
        if e.get('type') != 'current':
            return False
        if needs_recalc:
            return inputs_complete(fla_map.get(f), hp_map.get(f), rf_map.get(f), cs_map.get(f))
        return e.get('complete', False)
    any_complete = any(file_is_complete(f) for f in selected_files)

    # 3 stacked charts (Current+Pressure / Power / Flow) when specs exist; otherwise just 1.
    if any_complete:
        fig_ts = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08, subplot_titles=["Current & Pressure Trends", "Calculated Power Trends", "Estimated Flowrate Trends"], specs=[[{"secondary_y": True}], [{}], [{}]])
    else:
        fig_ts = make_subplots(rows=1, cols=1, subplot_titles=["Current & Pressure Trends"], specs=[[{"secondary_y": True}]])
    color_seq = px.colors.qualitative.Plotly   # palette cycled through, one color per current file
    color_map = {}
    current_index = 0
    current_kpi_html_rows, pressure_kpi_html_rows = [], []   # KPI table rows, kept separate then concatenated

    current_files_sel, pressure_files_sel = [], []
    complete_files_sel = []  # current files that have all four specs -> Power/Flow/KPIs/Section 5
    overall_power_sum, overall_flow_sum = 0, 0   # running totals for the system-wide efficiency row
    has_current = False
    td_style = {'padding': '10px', 'border': '1px solid #d6d6d6', 'textAlign': 'center'}   # shared table-cell style

    # ---- Main per-file loop: process data, add chart traces, build KPI rows ----
    for fname in selected_files:
        if fname not in trend_dfs: continue
        entry = trend_dfs[fname]
        df_raw, xaxis, yaxis, trend_type = entry["df"], entry["xaxis"], entry["yaxis"], entry["type"]

        # ---- Heavy computation, done only when needed, then cached in entry["df_processed"] ----
        if needs_recalc or "df_processed" not in entry:
            df = df_raw.copy()

            # Estimate the logging interval (seconds between samples) to convert the
            # MA window from minutes into a number of samples.
            median_sec = df[xaxis].diff().dt.total_seconds().median()
            if pd.isna(median_sec) or median_sec <= 0: median_sec = 60

            window_samples = max(2, int((ma_val * 60) / median_sec))

            if trend_type == "current":
                # Current & its MA never depend on the specs, so always compute them.
                df['Current_MA'] = zero_phase_ma_filter(df[yaxis], window_samples)

                # Power/Flow (and their MAs) only when all four specs are provided.
                complete = inputs_complete(fla_map.get(fname), hp_map.get(fname), rf_map.get(fname), cs_map.get(fname))
                entry["complete"] = complete   # cache so zoom (no recalc) knows this file's status
                if complete:
                    FLA, HP, RF, CS = float(fla_map.get(fname)), float(hp_map.get(fname)), float(rf_map.get(fname)), float(cs_map.get(fname))
                    pf, percent_capacity = get_power_factor_equation(HP), from_performance_curve_equation(CS)
                    df['frac_FLA'] = df[yaxis] / FLA                                   # current as a fraction of full-load amps
                    df['Power'] = pf(df['frac_FLA']) * math.sqrt(3) * V * df[yaxis] / 1000   # 3-phase power in kW
                    df['frac_RP'] = df['Power'] / (pf(1) * math.sqrt(3) * V * FLA / 1000)     # power as a fraction of rated power
                    df['Flow'] = percent_capacity(df['frac_RP']*100) * RF / 100        # air flow via the control-scheme curve

                    df['Power_MA'] = zero_phase_ma_filter(df['Power'], window_samples)
                    df['Flow_MA'] = zero_phase_ma_filter(df['Flow'], window_samples)

            elif trend_type == "pressure":
                df['Pressure_Raw'] = df[yaxis] * (6.89 if amplify_pressure else 1)   # optional kPa -> psi conversion
                df['Pressure_MA'] = zero_phase_ma_filter(df['Pressure_Raw'], window_samples)

            entry["df_processed"] = df   # cache the processed frame for fast zoom/pan
        else:
            df = entry["df_processed"]   # reuse cached result (zoom/pan path)

        # KPIs are computed over the visible window only (or the whole series if not zoomed).
        if zoom_xrange:
            mask = (df[xaxis] >= zoom_xrange[0]) & (df[xaxis] <= zoom_xrange[1])
            region_kpi = df[mask]
        else:
            region_kpi = df

        # Downsample for plotting (keeps the figure responsive on large logs).
        if trend_type == "current":
            plot_df = downsample_df_lttb(region_kpi, xaxis, yaxis, max_points=10000)
        else:
            plot_df = downsample_df_lttb(region_kpi, xaxis, 'Pressure_Raw', max_points=10000)

        base_name = fname.split('.')[0]
        if trend_type == "current":
            has_current = True
            current_files_sel.append(fname)
            complete = entry.get("complete", False)
            if complete:
                complete_files_sel.append(fname)
            # Assign a stable color per file; the MA line is a darker shade of it.
            if fname not in color_map:
                color_map[fname] = color_seq[current_index % len(color_seq)]
                current_index += 1
            base_color = color_map[fname]
            dark_color = darken_color(base_color, 0.6)

            # Current trend (row 1) is always plotted.
            fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df[yaxis].to_numpy(), mode='lines', name=f"{base_name} [Raw Current]", line=dict(color=base_color, width=1.5), opacity=0.4), row=1, col=1, secondary_y=False)
            if show_ma:
                fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Current_MA'].to_numpy(), mode='lines', name=f"{base_name} [{ma_val}m MA Current]", line=dict(color=dark_color, width=2.5)), row=1, col=1, secondary_y=False)

            if complete:
                # Power (row 2) & Flow (row 3) trends only with complete specs.
                fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Power'].to_numpy(), mode='lines', name=f"{base_name} [Raw Power]", line=dict(color=base_color, width=1.5, dash='dash'), opacity=0.4), row=2, col=1)
                if show_ma:
                    fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Power_MA'].to_numpy(), mode='lines', name=f"{base_name} [{ma_val}m MA Power]", line=dict(color=dark_color, width=2.5, dash='dash')), row=2, col=1)

                fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Flow'].to_numpy(), mode='lines', name=f"{base_name} [Raw Flow]", line=dict(color=base_color, width=1.5, dash='dot'), opacity=0.4), row=3, col=1)
                if show_ma:
                    fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Flow_MA'].to_numpy(), mode='lines', name=f"{base_name} [{ma_val}m MA Flow]", line=dict(color=dark_color, width=2.5, dash='dot')), row=3, col=1)

                # ---- KPI math over the visible window ----
                ON_df = region_kpi[region_kpi[yaxis] > 5]   # "ON" = current above 5 A (compressor running)
                median_sec = region_kpi[xaxis].diff().dt.total_seconds().median() if len(region_kpi) > 1 else 60

                hours_ON = len(ON_df) * (median_sec / 3600.0)        # running time in hours
                total_hours = len(region_kpi) * (median_sec / 3600.0)  # total recorded time in hours
                duty_factor = (hours_ON / total_hours) * 100 if total_hours > 0 else 0   # % of time running

                FLA = float(fla_map.get(fname))
                HP = float(hp_map.get(fname))
                pf = get_power_factor_equation(HP)
                RP = pf(1) * math.sqrt(3) * V * FLA / 1000   # rated power (kW) at full load

                mean_power_ON = ON_df['Power'].mean() if len(ON_df) > 0 else 0
                mean_flow_ON = ON_df['Flow'].mean() if len(ON_df) > 0 else 0

                load_factor = (mean_power_ON / RP) * 100 if RP > 0 else 0   # avg running power vs rated power

                kWh_ON = mean_power_ON * hours_ON                              # energy used while running
                spec_eff = mean_power_ON / mean_flow_ON if mean_flow_ON > 0 else np.nan   # kW per cfm
                spec_eff_str = f"{spec_eff:.2f}" if not np.isnan(spec_eff) else "N/A"
                overall_power_sum += ON_df['Power'].sum()   # accumulate for the system-wide efficiency row
                overall_flow_sum += ON_df['Flow'].sum()

                # 7 KPI rows for this compressor; the Specific Efficiency cell spans all 7 (rowSpan=7).
                current_kpi_html_rows.extend([
                    html.Tr([html.Td(f"{base_name} Current (Amp)", style=td_style), html.Td(f"{region_kpi[yaxis].mean():.2f}", style=td_style), html.Td(f"{region_kpi[yaxis].min():.2f}", style=td_style), html.Td(f"{region_kpi[yaxis].max():.2f}", style=td_style), html.Td(spec_eff_str, rowSpan=7, style={'padding': '10px', 'border': '1px solid #d6d6d6', 'textAlign': 'center', 'verticalAlign': 'middle', 'fontWeight': 'bold', 'backgroundColor': '#f8f9fa'})]),
                    html.Tr([html.Td(f"{base_name} Power (kW)", style=td_style), html.Td(f"{region_kpi['Power'].mean():.2f}", style=td_style), html.Td(f"{region_kpi['Power'].min():.2f}", style=td_style), html.Td(f"{region_kpi['Power'].max():.2f}", style=td_style)]),
                    html.Tr([html.Td(f"{base_name} Flow (acfm)", style=td_style), html.Td(f"{region_kpi['Flow'].mean():.2f}", style=td_style), html.Td(f"{region_kpi['Flow'].min():.2f}", style=td_style), html.Td(f"{region_kpi['Flow'].max():.2f}", style=td_style)]),
                    html.Tr([html.Td(f"{base_name} Energy (kWh)", style=td_style), html.Td(f"{kWh_ON:.2f}", colSpan=3, style=td_style)]),
                    html.Tr([html.Td(f"{base_name} Operating Hours", style=td_style), html.Td(f"{hours_ON:.2f}", colSpan=3, style=td_style)]),
                    html.Tr([html.Td(f"{base_name} Duty Factor", style=td_style), html.Td(f"{duty_factor:.1f} %", colSpan=3, style=td_style)]),
                    html.Tr([html.Td(f"{base_name} Load Factor", style=td_style), html.Td(f"{load_factor:.1f} %", colSpan=3, style=td_style)], style={'borderBottom': '3px solid #bdc3c7'})
                ])
            else:
                # Specs missing -> current stats only, no Power/Flow/efficiency.
                current_kpi_html_rows.append(
                    html.Tr([
                        html.Td(f"{base_name} Current (Amp)", style=td_style),
                        html.Td(f"{region_kpi[yaxis].mean():.2f}", style=td_style),
                        html.Td(f"{region_kpi[yaxis].min():.2f}", style=td_style),
                        html.Td(f"{region_kpi[yaxis].max():.2f}", style=td_style),
                        html.Td("N/A (specs missing)", style={**td_style, 'color': '#7f8c8d', 'fontStyle': 'italic'})
                    ], style={'borderBottom': '3px solid #bdc3c7'})
                )

        elif trend_type == "pressure":
            # Pressure is drawn on the secondary (right) y-axis of row 1.
            pressure_files_sel.append(fname)
            fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Pressure_Raw'].to_numpy(), mode='lines', name=f"{base_name} [Raw Pressure]", line=dict(color="#6D6D6D", width=1.5), opacity=0.6), row=1, col=1, secondary_y=True)
            if show_ma:
                fig_ts.add_trace(go.Scattergl(x=plot_df[xaxis].to_numpy(), y=plot_df['Pressure_MA'].to_numpy(), mode='lines', name=f"{base_name} [{ma_val}m MA Pressure]", line=dict(color="#373737", width=2.5)), row=1, col=1, secondary_y=True)

            region_press = region_kpi['Pressure_Raw']
            pressure_kpi_html_rows.append(html.Tr([html.Td(f"{base_name} Pressure (psig)", style=td_style), html.Td(f"{region_press.mean():.2f}", style=td_style), html.Td(f"{region_press.min():.2f}", style=td_style), html.Td(f"{region_press.max():.2f}", style=td_style), html.Td("-", style=td_style)], style={'borderBottom': '3px solid #bdc3c7'}))

    # Current KPI rows first, then pressure rows.
    kpi_html_rows = current_kpi_html_rows + pressure_kpi_html_rows

    # System-wide specific efficiency (total power / total flow), only if any file was complete.
    if complete_files_sel:
        combined_se = overall_power_sum / overall_flow_sum if overall_flow_sum > 0 else np.nan
        combined_se_str = f"{combined_se:.2f}" if not np.isnan(combined_se) else "N/A"
        kpi_html_rows.append(html.Tr([
            html.Td("OVERALL SYSTEM SPECIFIC EFFICIENCY (kW/cfm)", colSpan=4, style={'padding': '14px', 'backgroundColor': '#f39c12', 'color': 'white', 'fontWeight': 'bold', 'textAlign': 'right', 'border': '1px solid #e67e22', 'fontSize': '1.1em'}),
            html.Td(combined_se_str, style={'padding': '14px', 'backgroundColor': '#f39c12', 'color': 'white', 'fontWeight': 'bold', 'textAlign': 'center', 'border': '1px solid #e67e22', 'fontSize': '1.1em'})
        ]))

    # Weekend shading, derived from the first selected file's time axis.
    shapes = find_shapes(trend_dfs[selected_files[0]]['df'], trend_dfs[selected_files[0]]['xaxis']) if selected_files and selected_files[0] in trend_dfs else []

    # Axis titles + overall layout. yaxis3/yaxis4 (Power/Flow) only exist in the 3-row figure.
    # uirevision=True keeps the user's zoom when the figure is rebuilt. Legend sits above the plot.
    layout_kwargs = dict(
        shapes=shapes, yaxis=dict(title='Current (Amp)'), yaxis2=dict(title='Pressure (psig)'),
        height=1200 if any_complete else 450, uirevision=True,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="center", x=0.5)
    )
    if any_complete:
        layout_kwargs['yaxis3'] = dict(title='Power (kW)')
        layout_kwargs['yaxis4'] = dict(title='Flow (acfm)')
    fig_ts.update_layout(**layout_kwargs)
    fig_ts.update_xaxes(showticklabels=True, tickmode="auto", nticks=15, rangeslider=dict(visible=False))

    # ---- Section 5: Power-vs-Pressure density image ----
    # Only (re)built on Apply (not on zoom, to keep panning snappy). no_update means
    # "leave the previous image as-is". Requires a pressure log AND >=1 complete current file.
    if trigger == "apply-btn" or trigger is None:
        scatter_output = html.Div()
        if complete_files_sel and pressure_files_sel:
            pfile = pressure_files_sel[0]   # pair every compressor against the first pressure log
            df_p, p_x = trend_dfs[pfile]["df_processed"], trend_dfs[pfile]["xaxis"]
            n_comps = len(complete_files_sel)
            cols = 2 if n_comps > 1 else 1              # grid layout: up to 2 columns
            rows = math.ceil(n_comps / cols)
            fig, axes = plt.subplots(rows, cols, figsize=(7.5 * cols, 7.5 * rows), squeeze=False)

            for idx, pvsp_comp in enumerate(complete_files_sel):
                r, c = idx // cols, idx % cols
                ax = axes[r, c]
                df_c, cx = trend_dfs[pvsp_comp]["df_processed"], trend_dfs[pvsp_comp]["xaxis"]
                # Align each compressor's Power to the pressure log by nearest timestamp (within 30s).
                df_pp = pd.merge_asof(df_c[[cx, "Power"]].sort_values(cx), df_p[[p_x, "Pressure_Raw"]].sort_values(p_x), left_on=cx, right_on=p_x, direction="nearest", tolerance=pd.Timedelta("30s")).dropna()
                x, y = df_pp["Pressure_Raw"], df_pp["Power"]
                if len(x) > 0:
                    bins = 200
                    H, xedges, yedges = np.histogram2d(x, y, bins=bins)
                    # Render the density grid directly (fast, fixed 200x200 cost)
                    # instead of scattering every raw point (slow, scales with data size).
                    H_masked = np.ma.masked_where(H.T == 0, H.T)   # hide empty bins
                    sc = ax.pcolormesh(xedges, yedges, H_masked, cmap='viridis', norm=mc.LogNorm(vmin=1, vmax=H.max()))
                    fig.colorbar(sc, ax=ax, label='Local density (log scale)')

                ax.set_title(f'{pvsp_comp.split(".")[0]} Power vs Pressure')
                ax.set_xlabel('Pressure (psig)')
                ax.set_ylabel('Power (kW)')
                ax.xaxis.set_major_locator(MultipleLocator(10))
                ax.yaxis.set_major_locator(MultipleLocator(10))
                ax.grid(which='major', axis='both', linestyle='--', linewidth=1, alpha=0.7)

            # Remove any unused grid cells (e.g. 3 compressors in a 2x2 grid leaves one empty).
            for idx in range(n_comps, rows * cols):
                fig.delaxes(axes[idx // cols, idx % cols])

            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100)
            plt.close(fig)
            b64_data = base64.b64encode(buf.getbuffer()).decode("utf8")
            scatter_output = html.Img(src=f"data:image/png;base64,{b64_data}", style={"maxWidth": "100%", "height": "auto", "borderRadius": "5px"})
        else:
            scatter_output = html.Div("Requires at least 1 Pressure log and 1 Current log with all specs entered (FLA, HP, Rated Flow, Control Scheme). Fill those specs in Section 1 to generate this plot.", style={'color': 'gray', 'fontStyle': 'italic', 'padding': '50px'})
    else:
        scatter_output = no_update   # zoom/pan: keep the existing Section 5 image

    # ---- Assemble the KPI table (Section 3) ----
    kpi_table_header = html.Thead(html.Tr([
        html.Th("Metric", style={'padding': '12px', 'backgroundColor': '#2c3e50', 'color': 'white', 'border': '1px solid #2c3e50', 'textAlign': 'center'}),
        html.Th("Mean", style={'padding': '12px', 'backgroundColor': '#2c3e50', 'color': 'white', 'border': '1px solid #2c3e50', 'textAlign': 'center'}),
        html.Th("Min", style={'padding': '12px', 'backgroundColor': '#2c3e50', 'color': 'white', 'border': '1px solid #2c3e50', 'textAlign': 'center'}),
        html.Th("Max", style={'padding': '12px', 'backgroundColor': '#2c3e50', 'color': 'white', 'border': '1px solid #2c3e50', 'textAlign': 'center'}),
        html.Th("Specific Efficiency (kW/cfm)", style={'padding': '12px', 'backgroundColor': '#2c3e50', 'color': 'white', 'border': '1px solid #2c3e50', 'textAlign': 'center'})
    ]))
    kpi_table = html.Table([kpi_table_header, html.Tbody(kpi_html_rows)], style={'width': '100%', 'borderCollapse': 'collapse', 'fontFamily': 'sans-serif', 'backgroundColor': 'white', 'borderRadius': '5px', 'overflow': 'hidden'})

    # Duty/Load factor info bar: only shown when those KPIs are actually present.
    # (Hover the ℹ️ icons to see the definitions via the `title` tooltip.)
    if complete_files_sel:
        info_bar = html.Div([
            html.Span([
                html.Span("ℹ️", style={'cursor': 'help', 'marginRight': '5px'}),
                html.Strong("Duty Factor "),
                #html.Span("Ratio of total operating hours (when compressor is ON) to total recorded hours.", style={'color': '#7f8c8d'})
            ], title="Duty factor is a ratio of total operating hours (when compressor is ON) to total recorded hours.", style={'marginRight': '30px', 'display': 'inline-block'}),
            html.Span([
                html.Span("ℹ️", style={'cursor': 'help', 'marginRight': '5px'}),
                html.Strong("Load Factor "),
                #html.Span("Ratio of average operational power to rated power.", style={'color': '#7f8c8d'})
            ], title="Load factor is a ratio of average power (when compressor is ON) to rated power.", style={'display': 'inline-block'})
        ], style={'fontSize': '0.9em', 'background': '#e8f4f8', 'padding': '10px 12px', 'borderRadius': '5px', 'marginBottom': '15px'})
        kpi_output = html.Div([info_bar, kpi_table])
    else:
        kpi_output = kpi_table

    # Three return values map to the three Output()s: figure, Section 5 image, KPI table.
    return fig_ts, scatter_output, kpi_output


# =============================================================================
# ENTRY POINT
# =============================================================================
# Starts the local development server. debug=True enables auto-reload + in-browser errors.
# To share on your network use app.run(debug=True, host="0.0.0.0"); change port with port=8060.
if __name__ == '__main__':
    app.run(debug=True)
