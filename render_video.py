#!/usr/bin/env python3
"""
render_video.py — ECharts + Playwright 视频录制器
读取 CSV → 驱动 ECharts → 逐帧截图 → ffmpeg 合成 MP4

Usage:
  python render_video.py line   <date> <session>   # 折线图视频
  python render_video.py bar    <date> <session>   # 柱状图视频
"""
import subprocess, json, os, sys, time, math, shutil, tempfile
import pandas as pd
import numpy as np
from pathlib import Path
from playwright.sync_api import sync_playwright
from scipy.interpolate import CubicSpline

BASE_DIR = Path("/Users/wangxianshuo/Projects/personal/a-stock-capital-flow")
DATA_DIR = BASE_DIR / "data"
VIDEO_DIR = BASE_DIR / "videos"
TEMPLATE_DIR = BASE_DIR / "templates"
ASSETS_DIR = BASE_DIR / "assets"
FPS = 48
OUTPUT_FPS = 30
CANVAS_W, CANVAS_H = 1080, 1920

# BGM 按星期几选择：周一~周五
BGM_WEEKDAY = ["bgm_mon.mp3", "bgm_tue.mp3", "bgm_wed.mp3", "bgm_thu.mp3", "bgm_fri.mp3"]


def get_bgm_path(date_str):
    """根据日期返回对应工作日的 BGM 路径（周末默认周一）"""
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    idx = dt.weekday()
    if idx < 0 or idx > 4:
        idx = 0
    bgm = ASSETS_DIR / BGM_WEEKDAY[idx]
    return bgm if bgm.exists() else None

# 高饱和霓虹色系（深色背景专用）
COLORS_20 = [
    "#FF4D4F", "#1890FF", "#52C41A", "#FAAD14", "#722ED1",
    "#13C2C2", "#EB2F96", "#2F54EB", "#FF7A45", "#9254DE",
    "#36CFC9", "#F759AB", "#597EF7", "#73D13D", "#FFA940",
    "#B37FEB", "#87E8DE", "#FF85C0", "#FFC53D", "#5CDBD3",
]


def load_data(date_str, session):
    csv_path = DATA_DIR / date_str / f"concept_flow_{session}.csv"
    if not csv_path.exists():
        print(f"No data: {csv_path}")
        return None
    df = pd.read_csv(csv_path)
    if df.empty or "timestamp" not in df.columns:
        return None
    timestamps = df["timestamp"].tolist()
    sector_cols = [c for c in df.columns if c != "timestamp"
                   and not c.startswith("__SSE")]
    if not sector_cols:
        return None
    final_vals = {c: df[c].dropna().iloc[-1] if not df[c].dropna().empty else 0
                  for c in sector_cols}
    sorted_sectors = sorted(final_vals, key=lambda c: abs(final_vals[c]), reverse=True)[:20]
    data = {}
    for name in sorted_sectors:
        data[name] = df[name].values.astype(float)
    all_vals = [v for n in sorted_sectors for v in data[n] if not np.isnan(v)]
    if not all_vals:
        return None
    y_min, y_max = min(all_vals), max(all_vals)
    margin = (y_max - y_min) * 0.10
    ylim_bottom = math.floor((y_min - margin) / 50) * 50
    ylim_top = math.ceil((y_max + margin) / 50) * 50
    # 读取上证指数实时数据（从 CSV 最后有效行）
    sse_price = "--"
    sse_change_str = "--"
    if "__SSE_PRICE__" in df.columns:
        sse_col = df["__SSE_PRICE__"].dropna()
        if not sse_col.empty:
            sse_price = f"{sse_col.iloc[-1]:.2f}"
    if "__SSE_CHANGE__" in df.columns and "__SSE_PCT__" in df.columns:
        chg_col = df["__SSE_CHANGE__"].dropna()
        pct_col = df["__SSE_PCT__"].dropna()
        if not chg_col.empty and not pct_col.empty:
            chg = chg_col.iloc[-1]
            pct = pct_col.iloc[-1]
            sign = "+" if chg >= 0 else ""
            sign_p = "+" if pct >= 0 else ""
            sse_change_str = f"{sign}{chg:.2f}  {sign_p}{pct:.2f}%"

    return timestamps, data, (ylim_bottom, ylim_top), sorted_sectors, sse_price, sse_change_str


def prepare_line_data(timestamps, data, ylim, sectors, target_points=600):
    """对折线数据做三次样条插值，让动画更平滑"""
    n = len(timestamps)
    x = np.linspace(0, 1, n)
    x_new = np.linspace(0, 1, target_points)

    new_timestamps = []
    step = max(1, target_points // (n - 1)) if n > 1 else target_points
    for i in range(target_points):
        src_idx = min(int(round(i / (target_points - 1) * (n - 1))), n - 1)
        new_timestamps.append(timestamps[src_idx])

    sectors_json = []
    for i, name in enumerate(sectors):
        vals = data[name].astype(float)
        # 填充前面的 NaN（如果有）
        mask = ~np.isnan(vals)
        if mask.any():
            first_valid = np.where(mask)[0][0]
            vals[:first_valid] = vals[first_valid]
        cs = CubicSpline(x, vals)
        interp = cs(x_new)
        # is_in 基于最终值的正负，而非 idx 位置
        final_val = float(vals[-1]) if not np.isnan(vals[-1]) else 0.0
        sectors_json.append({
            "name": name,
            "values": [round(float(v), 2) for v in interp],
            "isIn": final_val >= 0,
        })
    return {"timestamps": new_timestamps, "sectors": sectors_json, "ylim": [ylim[0], ylim[1]]}


def prepare_bar_data(data, sectors):
    """
51c6备柱状图数据：净流入/流出各 TOP10，每个分区独立计算百分比"""
    pairs = [(n, float(data[n][-1])) for n in sectors if not np.isnan(data[n][-1])]
    top_in = sorted([p for p in pairs if p[1] >= 0], key=lambda x: -x[1])[:10]
    top_out = sorted([p for p in pairs if p[1] < 0], key=lambda x: x[1])[:10]

    in_max = max([v for _, v in top_in]) if top_in else 1
    out_max = max([abs(v) for _, v in top_out]) if top_out else 1

    in_items = [{"name": n, "value": round(v, 2), "pct": min(100, round(v / in_max * 100, 2))}
                for n, v in top_in]
    out_items = [{"name": n, "value": round(v, 2), "pct": min(100, round(abs(v) / out_max * 100, 2))}
                 for n, v in top_out]

    return {"in": in_items, "out": out_items}


def render_line_video(timestamps, data, ylim, sectors, date_str, session, out_path, sse_price="--", sse_change_str="--"):
    label = "午盘" if session == "morning" else "收盘"
    date_display = f"{date_str[5:7]}月{date_str[8:10]}日"

    chart_data = prepare_line_data(timestamps, data, ylim, sectors)
    chart_data["dateText"] = date_display
    chart_data["sessionTag"] = label
    chart_data["indexValue"] = sse_price
    chart_data["indexChange"] = sse_change_str

    tmpdir = tempfile.mkdtemp(prefix="video_line_")
    html_path = TEMPLATE_DIR / "line_chart.html"

    INTRO_SEC = 2.0
    ANIM_SEC = 18.0
    FREEZE_SEC = 3.0
    title_frames = int(INTRO_SEC * FPS)
    anim_frames = int(ANIM_SEC * FPS)
    freeze_frames = int(FREEZE_SEC * FPS)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        page.evaluate(f"window.__set_data({json.dumps(chart_data, ensure_ascii=False)})")
        page.wait_for_timeout(500)

        page.evaluate("window.__set_progress(0)")
        page.wait_for_timeout(300)
        for f in range(title_frames):
            page.screenshot(path=f"{tmpdir}/frame_{f:05d}.png", type="png")

        for f in range(anim_frames):
            raw = (f + 1) / anim_frames
            t = raw ** 0.7 / (raw ** 0.7 + (1 - raw) ** 0.7)
            page.evaluate(f"window.__set_progress({t})")
            page.wait_for_timeout(15)
            page.screenshot(path=f"{tmpdir}/frame_{title_frames + f:05d}.png", type="png")
            if (f + 1) % 100 == 0:
                print(f"  {f+1}/{anim_frames}")

        page.evaluate("window.__set_progress(1.0)")
        page.wait_for_timeout(300)
        for f in range(freeze_frames):
            page.screenshot(path=f"{tmpdir}/frame_{title_frames + anim_frames + f:05d}.png", type="png")

        browser.close()

    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", f"{tmpdir}/frame_%05d.png",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(OUTPUT_FPS), out_path
    ], capture_output=True)

    dur = INTRO_SEC + ANIM_SEC + FREEZE_SEC
    bgm = get_bgm_path(date_str)
    if bgm:
        bgm_out = out_path.replace(".mp4", "_bgm.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", out_path, "-i", str(bgm),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-af", "volume=0.5", bgm_out
        ], capture_output=True)
        os.replace(bgm_out, out_path)

    shutil.rmtree(tmpdir)
    print(f"Line video: {out_path} ({dur:.1f}s)")
    return out_path


def render_bar_video(data, sectors, date_str, session, out_path, sse_price="--", sse_change_str="--"):
    label = "午盘" if session == "morning" else "收盘"
    date_display = f"{date_str[5:7]}月{date_str[8:10]}日"

    bar_data = prepare_bar_data(data, sectors)
    bar_data["dateText"] = date_display
    bar_data["sessionTag"] = label
    bar_data["indexValue"] = sse_price
    bar_data["indexChange"] = sse_change_str

    tmpdir = tempfile.mkdtemp(prefix="video_bar_")
    html_path = TEMPLATE_DIR / "bar_chart.html"

    INTRO_SEC = 2.0
    GROW_SEC = 10.0
    FREEZE_SEC = 3.0
    intro_frames = int(INTRO_SEC * FPS)
    grow_frames = int(GROW_SEC * FPS)
    freeze_frames = int(FREEZE_SEC * FPS)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CANVAS_W, "height": CANVAS_H})
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        page.evaluate(f"window.__set_bar_data({json.dumps(bar_data, ensure_ascii=False)})")
        page.wait_for_timeout(500)

        page.evaluate("window.__set_bar_progress(0)")
        page.wait_for_timeout(300)
        for f in range(intro_frames):
            page.screenshot(path=f"{tmpdir}/frame_{f:05d}.png", type="png")

        for f in range(grow_frames):
            raw = (f + 1) / grow_frames
            t = raw ** 0.7 / (raw ** 0.7 + (1 - raw) ** 0.7)
            page.evaluate(f"window.__set_bar_progress({t})")
            page.wait_for_timeout(15)
            page.screenshot(path=f"{tmpdir}/frame_{intro_frames + f:05d}.png", type="png")
            if (f + 1) % 100 == 0:
                print(f"  {f+1}/{grow_frames}")

        page.evaluate("window.__set_bar_progress(1.0)")
        page.wait_for_timeout(300)
        for f in range(freeze_frames):
            page.screenshot(path=f"{tmpdir}/frame_{intro_frames + grow_frames + f:05d}.png", type="png")

        browser.close()

    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", f"{tmpdir}/frame_%05d.png",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(OUTPUT_FPS), out_path
    ], capture_output=True)

    dur = INTRO_SEC + GROW_SEC + FREEZE_SEC
    bgm = get_bgm_path(date_str)
    if bgm:
        bgm_out = out_path.replace(".mp4", "_bgm.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", out_path, "-i", str(bgm),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-af", "volume=0.5", bgm_out
        ], capture_output=True)
        os.replace(bgm_out, out_path)

    shutil.rmtree(tmpdir)
    print(f"Bar video: {out_path} ({dur:.1f}s)")
    return out_path


def main():
    if len(sys.argv) < 4:
        print("Usage: render_video.py <line|bar> <date> <session>")
        sys.exit(1)

    chart_type = sys.argv[1]
    date_str = sys.argv[2]
    session = sys.argv[3]

    result = load_data(date_str, session)
    if result is None:
        print(f"No data for {date_str}/{session}")
        sys.exit(1)
    timestamps, data, ylim, sectors, sse_price, sse_change_str = result
    print(f"Data: {len(timestamps)} points, {len(sectors)} sectors, ylim={ylim}, SSE={sse_price}")

    out_dir = VIDEO_DIR / date_str
    os.makedirs(out_dir, exist_ok=True)

    if chart_type == "line":
        label_en = "midday" if session == "morning" else "close"
        out_path = str(out_dir / f"{date_str}_{label_en}_line.mp4")
        render_line_video(timestamps, data, ylim, sectors, date_str, session, out_path, sse_price, sse_change_str)
    elif chart_type == "bar":
        label_en = "midday" if session == "morning" else "close"
        out_path = str(out_dir / f"{date_str}_{label_en}_bar.mp4")
        render_bar_video(data, sectors, date_str, session, out_path, sse_price, sse_change_str)
    else:
        print(f"Unknown type: {chart_type}")
        sys.exit(1)


if __name__ == "__main__":
    main()
