# 因为旅行民宿自动化

## 功能
1. 每日自动抓取携程竞品民宿价格/库存
2. 生成调价建议报告
3. 推送至飞书摘要通知并等待审批
4. 审批通过后自动执行 ebooking 改价
5. 每 3 分钟自动巡检携程会话并执行客服回复

## 目录结构
```
travel-automation/
├── competitor-analysis/     # 携程数据抓取
│   ├── config.json         # Cookie 配置（需定期更新）
│   ├── scrape_ctrip_v2.py  # 抓取脚本
│   └── results/            # 抓取结果
├── pricing/               # 调价引擎
│   ├── engine.py          # 调价逻辑
│   ├── ebooking_batch_price_api.py  # ebooking 批量改价 API 客户端
│   ├── recommendation_to_execution_plan.py  # 建议报告 -> ebooking 执行计划
│   ├── execute_saved_plan.py  # 执行已审核的保存计划
│   ├── ebooking_room_mapping.json  # 保守房型映射配置
│   └── recommendation_*.md  # 每日建议报告
├── feishu_push.py         # 飞书建议推送与审批记录落盘
├── feishu_approval.py     # 单条审批检查/执行
├── feishu_approval_worker.py  # 周期扫描 pending 审批
├── approval_worker.sh     # 审批 worker shell 包装器
├── auto_reply.py          # 携程会话自动回复主脚本
├── auto_reply_worker.sh   # 多账号自动回复调度器
├── auto_reply_accounts.example.json  # 客服账号配置示例
├── auto_reply_accounts.local.json  # 本地客服账号会话配置（已忽略）
├── travel_automation.env  # 可提交的默认运行配置
├── travel_automation.local.env  # 本地敏感覆盖配置（已忽略）
├── logs/                 # 运行日志
├── runner.sh             # 每日定时执行脚本
└── crontab-setup.sh      # 定时任务安装脚本
```

## 设置步骤

### 1. 安装依赖
```bash
pip3 install playwright
playwright install chromium
pip3 install scrapling
```

### 2. 配置 Cookie
Cookie 会过期，需要定期更新。

更新方式：
1. 在浏览器登录携程
2. F12 → Application → Cookies → https://hotels.ctrip.com
3. 复制 Cookie 内容到 `competitor-analysis/config.json`

### 3. 设置定时任务
```bash
cd travel-automation
chmod +x runner.sh crontab-setup.sh
./crontab-setup.sh
```

默认每天 18:00 执行。

安装器还会额外维护一条 `*/3 * * * *` 的自动客服回复任务。

### 4. 查看结果
```bash
# 查看日志
tail -f travel-automation/logs/cron_$(date +\%Y\%m\%d).log

# 查看调价建议
ls -la travel-automation/pricing/recommendation_*.md
```

## ebooking 批量改价 API

已确认 `/rateplan/batchPriceSetting` 的核心接口链可直接调用，默认认证态按以下顺序解析：

1. `CTRIP_STORAGE_STATE`
2. `~/.openclaw/credentials/ctrip-ebooking-auth.json`
3. `~/.credentials/ctrip-ebooking-auth.json`

常用命令：

```bash
# 列出 ebooking 房型与 roomProductId
python3 pricing/ebooking_batch_price_api.py list-products

# 查询某个房型在指定日期范围的当前价格设置
python3 pricing/ebooking_batch_price_api.py get-price \
  --room-product-id 1520168772 \
  --start-date 2026-04-23 \
  --end-date 2026-04-24

# 构造改价请求但不提交（默认 dry-run）
python3 pricing/ebooking_batch_price_api.py set-price \
  --room-product-id 1520168772 \
  --start-date 2026-04-23 \
  --end-date 2026-04-24 \
  --sale-price 491

# 真正提交改价，只有显式加 --commit 才会写入
python3 pricing/ebooking_batch_price_api.py set-price \
  --room-product-id 1520168772 \
  --start-date 2026-04-23 \
  --end-date 2026-04-24 \
  --sale-price 499 \
  --commit
```

说明：

- `set-price` 默认会先读取当前佣金率/餐食/底价信息，再生成提交 payload。
- 真正提交后会自动轮询 `queryMainTaskInfoForDisplay`，并尽量回读 `querySubTaskByProductForDisplay`。

## 建议报告转 ebooking 执行计划

已补上一层保守映射，可以把最新 `recommendation_*.md` 转成 dry-run 执行计划：

```bash
# 读取最新建议报告，生成指定日期范围的 ebooking dry-run 计划
python3 pricing/recommendation_to_execution_plan.py \
  --start-date 2026-04-23 \
  --end-date 2026-04-24

# 只有显式加 --commit 才会真的提交改价
python3 pricing/recommendation_to_execution_plan.py \
  --start-date 2026-04-23 \
  --end-date 2026-04-24 \
  --commit
```

说明：

- 当前映射来自 `pricing/ebooking_room_mapping.json`，只覆盖了已高置信确认的 ebooking 子产品。
- dry-run 结果和执行结果会写入 `pricing/artifacts/ebooking_execution_plan_*.json` 与 `pricing/artifacts/ebooking_execution_result_*.json`。
- 仍未映射的 ebooking 子产品会明确保留在 `unmappedRoomProductIds` 中，不会被自动提交。
- `reference_ebooking_sale_prices` 会作为稳定基线价使用，避免同一份建议重复运行时出现价格滚动抬升。

## 映射候选生成

如果要继续检查还有没有可扩容的 ebooking 子产品，可以运行：

```bash
python3 pricing/suggest_ebooking_room_mapping.py
```

说明：

- 脚本会读取 live ebooking 产品目录和当前映射文件
- 输出 `pricing/artifacts/ebooking_mapping_suggestions_*.json/.md`
- 当前这份报告已经表明：在现有 3 个公开房型组下，剩余未映射产品基本都属于亲子/家庭/套房/钟点房/早餐变体，暂无新的高置信标准房型候选

## 执行已保存计划

对于已经人工审阅过的 dry-run 计划，可以通过保存计划执行器二次确认后再提交：

```bash
# 读取最新 dry-run 计划，只做执行预览
python3 pricing/execute_saved_plan.py

# 只执行某个源房型，仍然先走本地护栏校验
python3 pricing/execute_saved_plan.py \
  --source-room-name "大床房"

# 真正提交已保存计划，只有显式加 --commit 才会写入
python3 pricing/execute_saved_plan.py \
  --plan-file pricing/artifacts/ebooking_execution_plan_20260422_082901.json \
  --max-ops 6 \
  --commit
```

说明：

- `execute_saved_plan.py` 默认只做 dry-run，不会直接提交。
- 执行前会校验操作数上限和单个房型的变价幅度护栏。
- 可以按 `--source-room-name` 或 `--room-product-id` 缩小执行范围，便于分批确认。
- 执行结果会另存为 `pricing/artifacts/ebooking_execution_result_*.json`。

## Runner 可选计划生成

`runner.sh` 现在支持在每日建议生成后，按环境变量生成 ebooking dry-run 计划：

```bash
export TRAVEL_PLAN_START_DATE=2026-04-23
export TRAVEL_PLAN_END_DATE=2026-04-24
bash runner.sh
```

说明：

- 未设置这两个环境变量时，日常 cron 仍只会生成建议并推送摘要，不会擅自构造执行计划。
- 若当日已生成 dry-run 计划，`feishu_push.py` 会自动把计划摘要一起推送。
- 飞书消息会明确提示审批口令：回复“确认改价”批准执行，回复“取消改价”拒绝执行。

也支持按相对天数自动推导计划日期，更适合挂在每天 18:00 的定时任务里：

```bash
# 生成“明天到后天”的 dry-run 计划
export TRAVEL_PLAN_START_OFFSET_DAYS=1
export TRAVEL_PLAN_END_OFFSET_DAYS=2
bash runner.sh
```

说明：

- 如果同时设置了绝对日期和相对偏移，优先使用绝对日期。
- 只设置 `TRAVEL_PLAN_START_OFFSET_DAYS` 时，结束日期会默认等于开始日期。

## 自动客服回复

自动客服回复现在已经接入项目级调度，不再依赖手工写 crontab：

```bash
# 单次跑全部已配置账号
bash auto_reply_worker.sh

# 单独跑某个账号
python3 auto_reply.py hotel_1164390341
```

说明：

- 账号会话配置从 `auto_reply_accounts.local.json` 读取，示例见 `auto_reply_accounts.example.json`
- 酒店资料会按酒店 ID 从 `hotel_info/*.md` 读取，目录规则见 `hotel_info/README.md`
- 如果本地没有对应 `hotel_info/*.md`，脚本会继续尝试按当前酒店 ID 自动抓取公开携程详情页里的基础信息，用于地址/停车/Wi-Fi 等规则回复
- 飞书报警和 LLM 回复能力从 `travel_automation.local.env` 读取 `FEISHU_*` 与 `MINIMAX_API_KEY`
- worker 会按 `TRAVEL_AUTO_REPLY_ACCOUNTS` 顺序巡检账号，默认每 3 分钟执行一次
- worker 自带全局互斥锁，上一轮尚未结束时会自动跳过重叠运行，避免重复回复
- `auto_reply.py` 现在会先调用未读数接口，未读为 0 时直接跳过浏览器
- 未读检查已按账号使用各自 `hotelhst`，不再写死到单一酒店 ID
- 若无法从现有 cookie 字段推断到期时间，可在账号配置里补 `cookie_expires_at`
- 规则兜底回复也会读取酒店资料里的早餐/宠物/停车/泳池/入住时间等事实，不再共用一套硬编码答案
- 当前自动回复已改为“酒店客服规则优先，LLM仅做风格润色”，像价格、房态、地址、停车、早餐、宠物、入住退房等问题会先按酒店客服逻辑统一回答
- 回复规则已拆到 `reply_logic.py`，会话提取脚本统一收口到 `session_extract.py`，避免 `auto_reply.py` 再次膨胀或出现双份逻辑漂移

回归测试：

```bash
/opt/homebrew/bin/python3 -m py_compile auto_reply.py reply_logic.py session_extract.py
/opt/homebrew/bin/python3 -m unittest discover -s tests -v
```

覆盖内容：

- 酒店客服核心问句：价格、早餐、Wi-Fi、入住人数
- 历史语料过滤与危险话术拦截
- `extract_sessions.js` 的统一生成与关键跳过短语

## 飞书审批轮询与自动执行

`feishu_approval.py` 可以读取最新审批记录，轮询飞书私信回复，并在确认后调用保存计划执行器：

```bash
# 单次检查审批状态，不执行
python3 feishu_approval.py

# 连续轮询 10 分钟，只检查并回帖状态
python3 feishu_approval.py \
  --watch-seconds 600 \
  --notify

# 收到“确认改价”后真正执行已保存计划
python3 feishu_approval.py \
  --watch-seconds 600 \
  --notify \
  --commit
```

说明：

- 审批记录会保存到 `pricing/artifacts/feishu_approval_request_*.json`。
- 当前按飞书私信里的关键词判断审批结果：批准词默认包含“确认改价”，拒绝词默认包含“取消改价”。
- 只有在审批状态为 `approved` 且显式加了 `--commit` 时，才会真的调用 ebooking 改价执行。

## Runner 可选审批闭环

如果希望 `runner.sh` 在推送后继续等待飞书确认，可以显式设置这些环境变量：

```bash
export TRAVEL_PLAN_START_DATE=2026-04-23
export TRAVEL_PLAN_END_DATE=2026-04-24
export TRAVEL_APPROVAL_WATCH_SECONDS=600
export TRAVEL_APPROVAL_NOTIFY=1
export TRAVEL_APPROVAL_COMMIT=0
bash runner.sh
```

说明：

- `TRAVEL_APPROVAL_WATCH_SECONDS` 控制轮询窗口；未设置时不会进入审批轮询。
- `TRAVEL_APPROVAL_NOTIFY=1` 会在飞书原消息下回复当前审批/执行状态。
- `TRAVEL_APPROVAL_COMMIT=1` 才允许在审批通过后真正执行改价；默认建议先保持 `0` 做观测。

## 飞书配置

飞书配置现在统一从项目根目录的配置文件读取，不再依赖代码里的默认值：

```dotenv
FEISHU_APP_ID=your_app_id
FEISHU_APP_SECRET=your_app_secret
FEISHU_USER_OPEN_ID=your_target_open_id
```

推荐方式：

- 把非敏感默认值保留在 `travel_automation.env`
- 把飞书密钥和目标 `open_id` 写入 `travel_automation.local.env`
- shell 和 Python 入口都会按 `travel_automation.env` -> `travel_automation.local.env` 的顺序加载，后者可覆盖前者

可参考：

- `travel_automation.env`
- `travel_automation.local.env`
- `travel_automation.env.example`

## 幂等保护

审批执行链现在增加了 plan 级别的重复执行拦截：

- 每条审批记录会保存 `planDigest`
- 若另一条审批已经成功执行过同一份 plan，后续审批会标记为 `duplicate_blocked`
- 这样可以避免同一私聊里多条审批消息共享同一个“确认改价”回复时触发重复改价

## ⚠️ 注意事项

- **Cookie 有效期**：约 1 个月，过期后需重新配置
- **系统需联网**：抓取需要访问携程
- **Mac 需保持运行**：定时任务依赖本地 cron/launchd

## 下一步
- [ ] 扩大 ebooking 房型映射覆盖率，减少 `unmappedRoomProductIds`
- [ ] 添加更多竞品民宿
- [ ] 对接携程开放平台 API（更稳定）
