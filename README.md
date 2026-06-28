# A-Stock Capital Flow Video Pipeline

A股主力板块资金流向视频自动化流水线：从东方财富采集板块资金数据 → ECharts 渲染折线图+柱状图 → Playwright 录屏 → ffmpeg 合成 MP4 → 飞书推送。

## 功能

- 每分钟采集全部概念板块净流入/流出数据（东方财富 push2 API）
- 自动筛选净流入 TOP10 + 净流出 TOP10（共 20 个主力板块）
- 同步采集上证指数实时数据
- 生成两个 9:16 竖屏视频：
  - **折线图视频** — 深色科技风，20 条霓虹色折线逐点绘制，标签防重叠，三次样条插值平滑动画
  - **柱状图视频** — 上下分区（红/绿），排名勋章 + 发光进度条，数值跟随动画
- 按工作日自动切换 BGM
- 自动推送到飞书群

## 项目结构

```
a-stock-capital-flow/
├── collector.py           数据采集（东方财富 API + 文件锁 + CDN 防缓存）
├── render_video.py        视频渲染（ECharts + Playwright + ffmpeg）
├── send_feishu.py         飞书推送（urllib 纯标准库实现）
├── publish.sh             入口脚本（采集 → 渲染 → 推送）
├── templates/
│   ├── line_chart.html    折线图模板（20 色高区分度 + 标签防重叠）
│   └── bar_chart.html      柱状图模板（上下分区 + 动画生长）
├── assets/
│   └── bgm_{mon..fri}.mp3 工作日 BGM
├── data/                  采集数据（CSV，按日存储）
├── videos/               视频输出（MP4，按日存储）
└── logs/                 运行日志
```

## 依赖

```bash
pip install pandas numpy playwright scipy
python -m playwright install chromium
brew install ffmpeg
```

## 使用

```bash
# 采集（阻塞至午盘/收盘时间）
python collector.py morning    # 采集到 11:30
python collector.py afternoon  # 采集到 15:00

# 生成视频
python render_video.py line 2026-06-12 morning   # 折线图
python render_video.py bar  2026-06-12 morning    # 柱状图

# 一键执行（采集 + 视频 + 飞书推送）
./publish.sh morning
./publish.sh afternoon
```

## 视频规格

| 参数 | 值 |
|------|------|
| 分辨率 | 1080×1920（9:16 竖屏满屏） |
| 录制帧率 | 48 FPS |
| 输出帧率 | 30 FPS |
| 折线图时长 | 23s（2s intro + 18s 动画 + 3s 定格） |
| 柱状图时长 | 15s（2s intro + 10s 生长 + 3s 定格） |

## 定时任务

通过 Hermes Agent cron 调度，工作日自动运行：

| 任务 | 时间 | 说明 |
|------|------|------|
| 午盘 | 9:30 | 采集到 11:30，生成视频并推送 |
| 收盘 | 13:01 | 采集到 15:00，生成视频并推送 |

## 技术亮点

- **颜色逻辑**：基于最终值正负标记 `isIn`，不依赖数组位置，避免排序导致的颜色错配
- **标签防重叠**：3 轮迭代推开 + Y 轴范围 clamp，20 个标签不遮挡不丢失
- **数据插值**：`scipy.interpolate.CubicSpline` 将分钟级数据插值到 600 点，动画丝滑
- **CDN 防缓存**：URL 时间戳 + `--no-keepalive` + `Cache-Control: no-cache`
- **进程防重**：`fcntl.flock` 文件锁，防止重复启动
- **SSE 列保障**：fieldnames 始终包含上证指数列，即使首次采集失败也不遗漏

## License

MIT