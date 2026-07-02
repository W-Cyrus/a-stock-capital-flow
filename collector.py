#!/usr/bin/env python3
"""collector.py V4 — 主力板块资金流向采集器
- 采集全部概念板块，每分钟保存全量数据
- 不做 TOP20 筛选，render 阶段再按最终行情选取
- CSV 宽表：timestamp + 全部板块 + __SSE_* 元数据列
- 文件锁防重复，追加模式
"""
import subprocess, json, time, os, sys, logging, csv, fcntl
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/Users/wangxianshuo/Projects/personal/a-stock-capital-flow")
DATA_DIR = BASE_DIR / "data"
LOG_DIR  = BASE_DIR / "logs"
LOCK_DIR = Path("/tmp")

CURL_BIN = "/usr/bin/curl"
CURL_BASE = [
    CURL_BIN, "-s", "--noproxy", "*", "--max-time", "15",
    "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "-H", "Referer: https://data.eastmoney.com/",
    "-H", "Cache-Control: no-cache, no-store",
    "-H", "Pragma: no-cache",
]

# fs=m:90+t:3 概念板块，主力资金排序
API_TEMPLATE = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn={pn}&pz=100&po=1&np=1"
    "&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2"
    "&fid=f62"
    "&fs=m:90+t:3"
    "&fields=f12,f14,f62"
    "&_={ts}"
)

_lock_fd = None

# 上证指数实时数据
SSE_API = (
    "https://push2.eastmoney.com/api/qt/stock/get"
    "?secid=1.000001&fields=f43,f60,f170&_={ts}"
)


def fetch_sse_index(logger: logging.Logger) -> dict:
    """获取上证指数实时数据：最新价、涨跌额、涨跌幅"""
    ts = int(time.time() * 1000)
    url = SSE_API.format(ts=ts)
    args = CURL_BASE + [url]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            logger.warning("SSE index fetch failed")
            return {}
        data = json.loads(r.stdout)
        d = data.get("data", {})
        price = d.get("f43", 0) / 100
        prev_close = d.get("f60", 0) / 100
        change_pct = d.get("f170", 0) / 100
        change = round(price - prev_close, 2)
        return {
            "price": round(price, 2),
            "change": change,
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        logger.error(f"SSE index error: {e}")
        return {}


def setup_logging(session: str) -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(f"collector_{session}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(LOG_DIR / f"collector_{session}.log")
        fh.setFormatter(fmt); logger.addHandler(fh)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt); logger.addHandler(ch)
    return logger


def fetch_all_pages(logger: logging.Logger, max_retries: int = 3) -> dict:
    """分页获取全部板块净流入（亿元）→ {板块名: 净流入}
    每页失败后重试 max_retries 次，应对网络抖动。"""
    all_items = []
    page = 1
    ts = int(time.time() * 1000)

    while True:
        url = API_TEMPLATE.format(pn=page, ts=ts)
        args = CURL_BASE + [url]
        success = False
        for attempt in range(max_retries + 1):
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=20)
                if r.returncode != 0 or not r.stdout.strip():
                    if attempt < max_retries:
                        logger.warning(f"curl p{page} failed (attempt {attempt+1}/{max_retries+1}): rc={r.returncode}, retrying...")
                        time.sleep(1.5)
                        continue
                    logger.warning(f"curl p{page} failed after {max_retries+1} attempts: rc={r.returncode}")
                    break
                data = json.loads(r.stdout)
                d = data.get("data", {})
                items = d.get("diff", [])
                total = d.get("total", 0)
                if not items:
                    break
                all_items.extend(items)
                if len(items) < 100 or len(all_items) >= total:
                    success = True
                    break
                success = True
                break
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"fetch p{page} error (attempt {attempt+1}/{max_retries+1}): {e}, retrying...")
                    time.sleep(1.5)
                    continue
                logger.error(f"fetch p{page} error after {max_retries+1} attempts: {e}")
                break
        if not success:
            break
        page += 1
        time.sleep(0.3)

    result = {}
    for it in all_items:
        name = it.get("f14", "")
        flow_raw = it.get("f62", 0) or 0
        if name:
            result[name] = round(flow_raw / 1e8, 4)
    return result


def acquire_lock(session: str, logger: logging.Logger) -> bool:
    global _lock_fd
    lock_path = LOCK_DIR / f"a-stock-collector-{session}.lock"
    try:
        _lock_fd = open(str(lock_path), "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()) + "\n")
        _lock_fd.flush()
        return True
    except (IOError, OSError):
        logger.error(f"Another collector instance is running (lock: {lock_path})")
        return False


def collect(session: str):
    logger = setup_logging(session)

    if not acquire_lock(session, logger):
        logger.error("Lock acquisition failed, exiting.")
        sys.exit(1)

    if session == "morning":
        end_h = int(os.environ.get("COLLECTOR_END_H", "11"))
        end_m = int(os.environ.get("COLLECTOR_END_M", "30"))
    else:
        end_h = int(os.environ.get("COLLECTOR_END_H", "15"))
        end_m = int(os.environ.get("COLLECTOR_END_M", "0"))

    now = datetime.now()
    end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if now >= end_time:
        logger.info(f"Already past {end_h:02d}:{end_m:02d}, skipping")
        return

    date_str = now.strftime("%Y-%m-%d")
    day_dir = DATA_DIR / date_str
    os.makedirs(day_dir, exist_ok=True)
    csv_path = day_dir / f"concept_flow_{session}.csv"

    fieldnames = None
    if csv_path.exists() and csv_path.stat().st_size > 0:
        with open(csv_path, "r", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header and header[0] == "timestamp":
                fieldnames = header
                row_count = sum(1 for _ in reader)
                logger.info(f"Found existing CSV with {row_count} rows, appending")

    logger.info(f"Start collecting → {csv_path}  (end {end_h:02d}:{end_m:02d})")
    last_data_hash = None

    def save_row(data: dict, sse: dict):
        """写入一行数据到 CSV，处理 fieldnames 的动态扩展"""
        nonlocal fieldnames
        row = {"timestamp": datetime.now().strftime("%H:%M")}
        row.update(data)
        if sse:
            row["__SSE_PRICE__"] = sse["price"]
            row["__SSE_CHANGE__"] = sse["change"]
            row["__SSE_PCT__"] = sse["change_pct"]

        if fieldnames is None:
            fieldnames = ["timestamp"] + sorted(data.keys()) + ["__SSE_PRICE__", "__SSE_CHANGE__", "__SSE_PCT__"]
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
        else:
            new_cols = [k for k in sorted(data.keys()) if k not in fieldnames]
            if new_cols:
                with open(csv_path, "r", newline="") as f:
                    reader = csv.DictReader(f)
                    old_rows = list(reader)
                fieldnames = fieldnames[:-3] + new_cols + fieldnames[-3:]
                with open(csv_path, "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writeheader()
                    w.writerows(old_rows)
                logger.info(f"New sectors appeared: {new_cols}")

        with open(csv_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            w.writerow(row)
        logger.info(f"✓ {row['timestamp']}  |  {len(data)} sectors saved")

    while datetime.now() < end_time:
        loop_start = time.time()
        try:
            data = fetch_all_pages(logger)
            if not data:
                logger.warning("No data this round, retrying next minute")
                time.sleep(max(0, 60 - (time.time() - loop_start)))
                continue

            sample = sorted(data.items())[:5]
            data_hash = hash(str(sample))
            if data_hash == last_data_hash:
                logger.warning(f"Data identical to previous round (possible cache)! sample: {sample}")
            last_data_hash = data_hash

            sse = fetch_sse_index(logger)
            save_row(data, sse)

        except Exception as e:
            logger.error(f"Loop error: {e}")

        elapsed = time.time() - loop_start
        sleep_time = max(0, 60 - elapsed)
        time.sleep(sleep_time)

    # 收盘后补采最后一轮，确保拿到收盘价
    logger.info("Final collection at close...")
    try:
        data = fetch_all_pages(logger)
        if data:
            sse = fetch_sse_index(logger)
            save_row(data, sse)
    except Exception as e:
        logger.error(f"Final collection error: {e}")

    logger.info(f"Collection finished → {csv_path}")


if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else "morning"
    collect(session)
