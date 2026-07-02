#!/usr/bin/env python3
"""
render_video.py — ECharts + Playwright 视频录制器
读取 CSV → 驱动 ECharts → 逐帧截图 → ffmpeg 合成 MP4

Usage:
  python render_video.py line   <date> <session>   # 折线图视频
  python render_video.py bar    <date> <session>   # 柱状图视频
"""
import subprocess, json, os, sys, time, math, shutil, tempfile, re
import pandas as pd
import numpy as np
from pathlib import Path
from playwright.sync_api import sync_playwright
from scipy.interpolate import CubicSpline

BASE_DIR = Path("/Users/wangxianshuo/Projects/personal/a-stock-capital-flow")
DATA_DIR = BASE_DIR / "data"
VIDEO_DIR = BASE_DIR / "videos"
TEMPLATE_DIR = BASE_DIR / "templates"

# ---- 非主题板块过滤 ----
# 这些板块属于业绩统计、风格分类、指数、交易机制等，不是真正的投资主题，
# 排除后让视频只展示有实际资金流向意义的纯主题板块。
_EXCLUDED_PATTERNS = [
    # 业绩预告类
    r'^\d{4}(三季报|年报|一季报)(预增|预减|扭亏)$',
    # 涨跌停统计类
    r'^昨日(涨停|连板|首板|炸板|触板|高振幅|高换手|打二板以上表现)$',
    r'^最近多板$',
    # 新高统计类
    r'^(近期|历史|百日)新高$',
    # 市值风格类
    r'^(大盘|中盘|小盘|微盘)(股|价值|成长)$',
    r'^微盘精选$',
    # 估值/质量风格类
    r'^(价值|题材|权重|微利|周期|趋势|超跌|反转|破净|破发|百元|低价|次新|红利)股$',
    r'^ST股$', r'^B股$',
    r'^(长期|红利)破净股?$', r'^破增发价股$',
    # 投资风格类
    r'^(消费|科技|医药医疗|金融地产|先进制造)风格$',
    # 指数类
    r'^(上证(50|180|380)|HS300|中证500|深证100R|深成500|创业板综|央视50)_?$',
    r'^创业成份$',
    # 交易/机制类
    r'^(融资融券|转债标的|股权激励|股权转让)$',
    # 指数纳入类
    r'^(富时罗素|MSCI中国|标准普尔|沪股通|深股通)$',
    # 机构持仓类
    r'^(机构|基金|社保|QFII)重仓$',
    r'^(证金持股|养老金)$',
    # 跨市场类
    r'^(AB股|AH股|GDR)$',
    # 综合指标类
    r'^(茅指数|宁组合|行业龙头)$',
    # 杂项
    r'^(IPO受益|举牌|创投|独角兽|贬值受益|退税商店|东方财富热股|首发经济)$',
    r'^(北交所概念|科创板做市商|科创板做市股)$',
    r'^(央国企改革|中特估|中字头)$',  # 政策概念，非纯行业主题
]
_EXCLUDED_EXACT = set()
_compiled_patterns = [re.compile(p) for p in _EXCLUDED_PATTERNS]


def is_valid_sector(name):
    """返回 True 表示该板块是有效的投资主题板块"""
    if name in _EXCLUDED_EXACT:
        return False
    for pat in _compiled_patterns:
        if pat.match(name):
            return False
    return True

# ---- 核心热点白名单 ----
# 只在这 ~65 个当前热门主题板块中选取 TOP10 流入/流出。
# 覆盖 AI、机器人、低空、新能源、医药、军工、数字经济等核心赛道。
# 人工维护：定期根据市场主线增删。
HOT_SECTORS = {
    # ── AI 基础设施 (9) ──
    "CPO概念", "AI芯片", "算力概念", "存储芯片", "先进封装",
    "高带宽内存", "光刻机(胶)", "国产芯片", "EDA概念",
    # ── AI 应用 (6) ──
    "DeepSeek概念", "多模态AI", "AIGC概念", "AI智能体", "AI应用",
    "智谱AI概念",
    # ── 半导体 (4) ──
    "第三代半导体", "半导体概念", "氮化镓", "碳化硅",
    # ── 机器人 & 智造 (6) ──
    "人形机器人", "机器人概念", "机器视觉", "减速器",
    "工业母机", "新型工业化",
    # ── 低空 & 智驾 (5) ──
    "低空经济", "飞行汽车(eVTOL)", "无人驾驶", "无人机", "商业航天",
    # ── 新能源车 & 电池 (6) ──
    "固态电池", "新能源车", "高压快充", "锂电池概念",
    "钠离子电池", "麒麟电池",
    # ── 光伏 & 储能 (5) ──
    "光伏概念", "BC电池", "HJT电池", "TOPCon电池", "储能概念",
    # ── 通信 (4) ──
    "5G概念", "铜缆高速连接", "6G概念", "通信技术",
    # ── 华为链 (4) ──
    "华为概念", "华为昇腾", "华为海思", "鸿蒙概念",
    # ── 医药创新 (7) ──
    "创新药", "AI制药（医疗）", "减肥药", "合成生物",
    "CAR-T细胞疗法", "医疗器械概念", "单抗概念",
    # ── 军工 & 航天 (4) ──
    "军工", "大飞机", "航天航空", "军民融合",
    # ── 消费 & 资源 (4) ──
    "白酒", "黄金概念", "稀土永磁", "谷子经济",
    # ── 数字经济 (5) ──
    "数据要素", "数据安全", "信创", "数字经济", "东数西算",
    # ── 前沿科技 (4) ──
    "可控核聚变", "量子科技", "人脑工程", "氢能源",
    # ── 其他活跃主题 (8) ──
    "消费电子概念", "PCB", "玻璃基板", "MiniLED",
    "液冷概念", "智能穿戴", "虚拟现实", "海洋经济",
    # ── 补充（对标主流软件板块）(9) ──
    "锂矿概念", "券商概念", "MLCC", "人工智能",
    "网络游戏", "物联网", "数据中心", "电网概念", "化工原料",
}


def is_hot_sector(name):
    """返回 True 表示该板块在白名单中"""
    return name in HOT_SECTORS
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
    # 下午：合并上午数据，展示全天走势
    if session == "afternoon":
        morning_path = DATA_DIR / date_str / "concept_flow_morning.csv"
        if morning_path.exists():
            df_morning = pd.read_csv(morning_path)
            df = pd.concat([df_morning, df], ignore_index=True)
    if df.empty or "timestamp" not in df.columns:
        return None
    timestamps = df["timestamp"].tolist()
    sector_cols = [c for c in df.columns if c != "timestamp"
                   and not c.startswith("__SSE")
                   and is_valid_sector(c)
                   and is_hot_sector(c)]
    if not sector_cols:
        print("Warning: no hot sectors found! Falling back to all valid sectors.")
        sector_cols = [c for c in df.columns if c != "timestamp"
                       and not c.startswith("__SSE")
                       and is_valid_sector(c)]
    if not sector_cols:
        return None
    final_vals = {c: df[c].dropna().iloc[-1] if not df[c].dropna().empty else 0
                  for c in sector_cols}
    # 净流入/净流出各取 TOP10（分开选，不受绝对值大小影响）
    pos = [(c, v) for c, v in final_vals.items() if v >= 0]
    neg = [(c, v) for c, v in final_vals.items() if v < 0]
    top_in = [c for c, _ in sorted(pos, key=lambda x: -x[1])[:10]]
    top_out = [c for c, _ in sorted(neg, key=lambda x: x[1])[:10]]
    sorted_sectors = top_in + top_out
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
        # 前向+后向填充所有 NaN（部分板块只有少量数据点）
        vals_series = pd.Series(vals).ffill().bfill()
        vals = vals_series.values
        # 如果所有值都是 NaN（ffill/bfill 后仍是 NaN），跳过该板块
        if np.isnan(vals).all():
            continue
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
    """准备柱状图数据：净流入/流出各 TOP10，每个分区独立计算百分比"""
    pairs = []
    for n in sectors:
        vals = data[n]
        valid = vals[~np.isnan(vals)]
        if len(valid) > 0:
            pairs.append((n, float(valid[-1])))
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
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": CANVAS_W, "height": CANVAS_H}, device_scale_factor=1)
        context.add_init_script("document.fonts.ready = Promise.resolve();")
        page = context.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        page.evaluate(f"window.__set_data({json.dumps(chart_data, ensure_ascii=False)})")
        page.wait_for_timeout(500)

        page.evaluate("window.__set_progress(0)")
        page.wait_for_timeout(300)
        for f in range(title_frames):
            page.screenshot(path=f"{tmpdir}/frame_{f:05d}.png", type="png", timeout=0)

        for f in range(anim_frames):
            raw = (f + 1) / anim_frames
            t = raw ** 0.7 / (raw ** 0.7 + (1 - raw) ** 0.7)
            page.evaluate(f"window.__set_progress({t})")
            page.wait_for_timeout(15)
            page.screenshot(path=f"{tmpdir}/frame_{title_frames + f:05d}.png", type="png", timeout=0)
            if (f + 1) % 100 == 0:
                print(f"  {f+1}/{anim_frames}")

        page.evaluate("window.__set_progress(1.0)")
        page.wait_for_timeout(300)
        # 定格阶段: 开启防重叠模式，重绘一次让标签铺开
        page.evaluate("window.__freeze_mode = true; window.__set_progress(1.0)")
        page.wait_for_timeout(300)
        for f in range(freeze_frames):
            page.screenshot(path=f"{tmpdir}/frame_{title_frames + anim_frames + f:05d}.png", type="png", timeout=0)

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
            "-shortest", "-af", "volume=0.53", bgm_out
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
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": CANVAS_W, "height": CANVAS_H}, device_scale_factor=1)
        context.add_init_script("document.fonts.ready = Promise.resolve();")
        page = context.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        page.evaluate(f"window.__set_bar_data({json.dumps(bar_data, ensure_ascii=False)})")
        page.wait_for_timeout(500)

        page.evaluate("window.__set_bar_progress(0)")
        page.wait_for_timeout(300)
        for f in range(intro_frames):
            page.screenshot(path=f"{tmpdir}/frame_{f:05d}.png", type="png", timeout=0)

        for f in range(grow_frames):
            raw = (f + 1) / grow_frames
            t = raw ** 0.7 / (raw ** 0.7 + (1 - raw) ** 0.7)
            page.evaluate(f"window.__set_bar_progress({t})")
            page.wait_for_timeout(15)
            page.screenshot(path=f"{tmpdir}/frame_{intro_frames + f:05d}.png", type="png", timeout=0)
            if (f + 1) % 100 == 0:
                print(f"  {f+1}/{grow_frames}")

        page.evaluate("window.__set_bar_progress(1.0)")
        page.wait_for_timeout(300)
        for f in range(freeze_frames):
            page.screenshot(path=f"{tmpdir}/frame_{intro_frames + grow_frames + f:05d}.png", type="png", timeout=0)

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
            "-shortest", "-af", "volume=0.53", bgm_out
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
