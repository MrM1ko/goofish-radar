# 闲鱼新品监控 + 描述过滤 + 自动拍单 · 详细设计文档

> 版本 v5 ｜ 状态：待确认/实施中

## 1. 项目概述

一个常驻后台的 Windows 自动化工具，用于：

1. **按关键词**在闲鱼（`goofish.com`）搜索商品；
2. 按**最新发布**排序，持续扫描本轮新商品，直到遇到上一轮已见商品或达到扫描上限；
3. 读取商品标题、价格、描述，并判断：
   - 是否为目标商品本体；
   - 是否存在损坏/瑕疵；
   - 是否存在钓鱼/引流；
   - 是否属于空盒、配件、求购、租赁、定金等无效商品；
4. 对**通过过滤且价格低于该监控任务阈值**的新商品，可选择**自动拍下并生成待付款订单**；
5. **绝不自动付款**。拍单后立即停止在待付款状态并发送通知，由用户人工确认商品、卖家、价格、规格等信息后决定是否付款；
6. 通过**邮件**通知新商品、拍单结果、AI 异常、登录失效及风控暂停等事件。

### 核心定位

本项目不是全自动交易系统，而是：

```text
自动发现
→ 自动筛选
→ 自动生成待付款订单
→ 通知用户
→ 人工确认
→ 人工决定是否付款
```

核心目标是提高低价新品发现与占单速度，同时保留最终交易决策权。

### 核心设计原则

- 全程使用真实浏览器（Playwright）模拟人工操作；
- 不做接口逆向；
- 不硬闯验证码；
- 不自动支付；
- 尽量减少页面访问次数；
- 对同一商品全局去重，避免重复拍单；
- 对不确定商品优先通知而不是自动拍；
- 低频轮询 + 随机延时 + 每日限拍，降低平台风控概率。

---

## 2. 需求清单（决策已确认）

| 需求 | 决策 |
|---|---|
| 数据获取 | 无 Cookie 优先；若读不到再接受扫码登录复用会话 |
| 监控对象 | 配置文件 `monitors` 列表，每个监控任务独立配置 |
| 排序 | 最新发布 |
| 扫描方式 | 从最新开始扫描，遇到上一轮已见商品后停止；设置 `max_scan_items` 防止异常无限扫描 |
| 商品去重 | 按 `item_id` **全局去重**，不同关键词命中同一商品不会重复处理 |
| 商品身份过滤 | 排除空盒、配件、求购、租赁、定金、回收、维修等非目标商品 |
| 描述判断 | **规则词表 + AI 大模型两层** |
| AI 不可用 | 降级使用规则层；邮件中明确标记 AI 未完成检查 |
| 引流排除 | 保守：仅命中明确引流词才排除 |
| 拍单触发 | 通过过滤 + 价格低于当前 monitor 阈值 |
| 多规格商品 | 默认**不自动拍**，仅通知人工查看 |
| 拍单行为 | 创建待付款订单后立即停止，**绝不点击任何支付按钮** |
| 人工确认 | 用户收到通知后人工检查并决定是否付款 |
| 风控 | 保守：默认限拍 3 单/天，拍单间隔拉长 |
| 通知 | 邮件 |
| 运行 | 常驻轮询，默认每 5 分钟 |
| 存储 | 继续使用 JSON / JSONL，暂不引入数据库 |

---

## 3. 技术选型

- **语言**：Python 3.12
- **环境管理**：项目内 `uv`
- **浏览器自动化**：`playwright`
- **浏览器**：Chromium
- **HTTP / AI**：`requests`
- **邮件**：标准库 `smtplib` + `email`
- **配置**：JSON + 外置词表 `.txt`
- **存储**：
  - JSON：当前状态 / 去重 / 订单记录
  - JSONL：历史追加记录
- **测试**：`pytest`
- **运行方式**：Windows 常驻后台运行

---

## 4. 目录架构

```text
闲鱼/
├── run.py                        # 入口：常驻轮询，Ctrl+C 退出
├── requirements.txt
├── README.md
├── DESIGN.md
├── ENVIRONMENTS.md
├── .gitignore
│
├── config/
│   ├── search.json               # 本地搜索配置（Git 忽略）
│   ├── account.json              # 本地账号与密钥（Git 忽略）
│   ├── order.json                # 本地拍单配置（Git 忽略）
│   ├── examples/                 # 三份可提交的示例配置
│   └── wordlists/                # 损坏/引流/无效商品词表
│
├── browser/
│   ├── __init__.py
│   ├── selectors.py              # 所有页面选择器集中管理
│   ├── session.py                # 登录/会话复用
│   ├── searcher.py               # 搜索 + 最新排序 + 扫描
│   ├── detail.py                 # 详情页描述/价格/规格信息
│   └── order.py                  # 创建待付款订单
│
├── core/
│   ├── __init__.py
│   ├── settings.py               # 设置模型、加载与校验
│   ├── models.py                 # 数据模型
│   ├── dedupe.py                 # 全局 item_id 去重
│   ├── pipeline.py               # 主流程
│   ├── orderer.py                # 拍单决策 + 限流
│   ├── runtime.py                # 暂停状态 / 单实例锁
│   │
│   ├── filter/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── identity_filter.py    # 商品身份过滤
│   │   ├── rule_filter.py        # 损坏/引流规则
│   │   └── ai_filter.py          # AI 语义判断
│   │
│   └── notifier/
│       ├── __init__.py
│       ├── base.py
│       └── email.py
│
├── scripts/
│   ├── probe.py                  # 页面结构/排序/登录验证
│   └── login.py                  # 手动重新登录
│
├── data/
│   ├── storage_state.json
│   ├── seen.json
│   ├── history.jsonl
│   ├── orders.json
│   ├── runtime_state.json
│   ├── run.lock
│   ├── screenshots/
│   ├── debug/
│   └── logs/
│
└── tests/
    ├── test_identity_filter.py
    ├── test_rule_filter.py
    ├── test_dedupe.py
    └── test_orderer.py
```

---

## 5. 总体数据流

```text
run.py 主循环
    ↓
读取 monitors
    ↓
对每个 monitor 执行搜索
    ↓
最新发布排序
    ↓
从新到旧扫描商品
    ↓
遇到上一轮已见商品 / 达到 max_scan_items
    ↓
得到候选新商品
    ↓
全局 item_id 去重
    ↓
读取详情页
    ↓
商品身份过滤
    ↓
损坏 / 瑕疵 / 引流规则过滤
    ↓
AI 语义过滤（可选）
    ↓
价格判断
    ↓
是否存在多规格？
    ├─ 是 → 仅通知
    └─ 否
         ↓
       Orderer
         ↓
   每日限拍 + 拍单间隔
         ↓
      创建订单
         ↓
 ┌───────────────┬──────────────┐
 SUCCESS        UNKNOWN        FAILED
    ↓              ↓              ↓
待付款订单      不允许重拍      记录失败
    ↓              ↓              ↓
邮件通知        邮件人工检查    邮件通知
    ↓
人工确认
    ↓
人工决定是否付款
```

---

## 6. 配置体系

### 6.1 推荐配置结构

配置按职责拆分，启动时由 `core/settings.py` 合并并统一校验：

| 文件 | 内容 | 是否提交 Git |
|---|---|---|
| `config/search.json` | 轮询、浏览器搜索、监控任务 | 否 |
| `config/account.json` | 闲鱼登录、AI、邮件、微信通知 | 否 |
| `config/order.json` | 自动拍单开关和安全限额 | 否 |
| `config/examples/*.example.json` | 无真实凭据的填写模板 | 是 |

搜索任务不再使用一个全局 `keywords + max_price`，每个 monitor 独立配置。下面仅展示 `search.json` 的主体结构：

```json
{
  "poll_interval_minutes": 5,

  "search": {
    "sort": "time",
    "max_scan_items": 50,
    "headless": true,
    "random_delay_seconds": [3, 8],
    "screenshot": false
  },

  "monitors": [
    {
      "name": "iphone15",
      "keyword": "iPhone 15",
      "enabled": true,
      "auto_order": true,
      "max_price": 3500.0,
      "exclude_words": [
        "空盒",
        "包装盒",
        "求购",
        "租赁"
      ]
    },
    {
      "name": "rtx4070",
      "keyword": "RTX 4070",
      "enabled": true,
      "auto_order": true,
      "max_price": 2200.0,
      "exclude_words": [
        "散热器",
        "空盒",
        "维修",
        "求购"
      ]
    }
  ]
}
```

### 6.2 Monitor 配置说明

每个 monitor 独立包含：

| 字段 | 说明 |
|---|---|
| `name` | 内部唯一名称 |
| `keyword` | 闲鱼搜索关键词 |
| `enabled` | 是否启用 |
| `auto_order` | 是否允许自动创建待付款订单 |
| `max_price` | 当前关键词独立价格阈值 |
| `exclude_words` | 当前关键词特有排除词 |

后续如果有需要，还可以增加：

```json
{
  "include_words": [],
  "min_price": 0,
  "seller_keywords": [],
  "location": null
}
```

暂不在 v5 首版实现。

---

## 7. 词表设计

### 7.1 `config/wordlists/negative_words.txt`

用于明确的损坏、故障、瑕疵描述。

```text
磕碰
划痕
暗病
进水
维修过
屏幕碎
屏幕裂
电池鼓包
ID锁
不开机
无法开机
坏点
故障
```

### 7.2 `config/wordlists/traction_words.txt`

用于识别明显引流。

```text
加微信
加微
vx
v信
QQ
私聊
直播间
主页更多
另有
货到付款
```

### 7.3 `config/wordlists/invalid_item_words.txt`

用于识别不是目标商品本体的商品。

```text
空盒
包装盒
盒子
配件
维修
维修件
求购
回收
租赁
出租
定金
预售
订金
交换
置换
代购
```

### 7.4 词表规则

- 每行一个词或短语；
- `#` 开头为注释；
- 空行忽略；
- 支持：

```text
regex:<正则表达式>
```

Monitor 自己的 `exclude_words` 与全局 `invalid_item_words.txt` 合并判断。

---

## 8. 浏览器层

## 8.1 `session.py`

职责：

- 启动 Chromium；
- 创建 Playwright context；
- 加载 `storage_state.json`；
- 判断登录状态；
- 登录失效时切换有头模式扫码登录；
- 登录成功后重新保存会话；
- 识别验证码 / 滑块 / 风控页面。

流程：

```text
启动
↓
storage_state 存在？
├─ 否 → 扫码登录
└─ 是
    ↓
验证登录状态
    ├─ 成功 → 继续
    └─ 失败 → 扫码登录
```

若检测到验证码：

```text
记录 runtime_state
↓
暂停 30 分钟
↓
发送邮件
↓
本轮退出
```

不无限重试。

---

## 8.2 `searcher.py`

打开：

```text
https://www.goofish.com/search?q={keyword}
```

设置：

```text
最新发布
```

具体方式由 `probe.py` 实测后确定：

```text
URL 参数
或
页面点击
```

### 扫描逻辑

不再固定只读取 `top_n=10`。

改为：

```text
从最新商品开始读取
↓
逐个检查 item_id
↓
如果 item_id 在 seen 中
    ↓
说明已经到达上一轮边界
    ↓
停止继续向后扫描
```

同时设置：

```text
max_scan_items = 50
```

避免：

- 页面异常；
- 排序异常；
- 首次运行；
- 旧商品一直未出现；

导致扫描数量过大。

### 首次启动

由于没有历史边界：

```text
最多读取 max_scan_items 条
```

并全部记录为已见。

是否对首次启动商品进行自动拍单，建议默认：

```text
否
```

即：

```text
首次启动
→ 建立基线
→ 只记录
→ 下一轮开始才处理真正新商品
```

防止项目第一次运行时一次性拍大量历史商品。

### 提取字段

```text
item_id
title
price
url
publish_time
seller_desc
matched_keywords
```

---

## 8.3 `detail.py`

详情页负责读取：

```text
标题
描述全文
详情页价格
商品状态
规格信息
卖家基础信息（若稳定可获取）
```

返回：

```python
DetailResult
```

重点增加：

```text
has_sku
sku_count
```

### 多规格策略

如果检测到多个可选择规格：

```text
has_sku = true
```

v5 默认：

```text
不自动选择第一个
不自动创建订单
发送邮件通知
```

原因是多规格商品可能存在：

```text
手机 ¥3000
包装盒 ¥100
配件 ¥20
```

搜索页展示的价格不一定对应目标商品本体。

---

## 8.4 `order.py`

职责只有一个：

> 创建待付款订单。

流程：

```text
open(item_url)
↓
再次确认商品仍在售
↓
再次读取价格
↓
确认没有多规格
↓
点击立即购买
↓
进入订单确认页
↓
点击提交订单
↓
等待订单结果
↓
绝不点击支付
```

### 明确禁止

代码中不得实现：

```text
支付密码
付款按钮
支付宝确认
微信支付
免密支付
自动付款
```

进入待付款状态后即视为浏览器自动化任务结束。

---

## 9. 核心数据模型

### 9.1 `Product`

```python
@dataclass
class Product:
    item_id: str
    title: str
    price: float
    url: str
    publish_time: str | None
    desc: str | None
    matched_keywords: list[str]
```

注意：

```text
keyword
```

不再作为商品唯一归属。

同一个商品可以：

```text
matched_keywords = ["iPhone 15", "苹果15"]
```

但只处理一次。

---

## 9.2 `FilterResult`

```python
@dataclass
class FilterResult:
    passed: bool
    reasons: list[str]
    ai_checked: bool
```

例如：

```text
passed=True
reasons=[
    "未命中损坏词",
    "未命中引流词",
    "未命中无效商品词"
]
ai_checked=False
```

邮件中明确显示：

```text
AI检查：未执行 / 失败降级
```

方便人工付款前重点确认。

---

## 9.3 `OrderResult`

不再只使用 `success: bool`。

```python
@dataclass
class OrderResult:
    status: str
    order_id: str | None
    reason: str | None
```

`status` 只允许：

```text
success
failed
unknown
```

### `success`

明确检测到：

```text
订单号
或
待付款状态
```

### `failed`

明确检测到：

```text
商品下架
无法购买
提交失败
```

### `unknown`

发生：

```text
点击提交订单后页面超时
网络断开
页面结构异常
无法确认订单是否生成
```

时使用。

`unknown` 是非常重要的状态。

---

## 10. 全局去重设计

原设计：

```text
keyword
└── item_id
```

改为：

```text
item_id
└── 商品信息
```

### `seen.json`

推荐：

```json
{
  "123456789": {
    "first_seen": "2026-08-15T18:20:00",
    "last_seen": "2026-08-15T18:25:00",
    "last_price": 1999,
    "title": "RTX4070...",
    "matched_keywords": [
      "RTX 4070",
      "4070显卡"
    ]
  }
}
```

判断：

```python
is_new(item_id) -> bool
```

只根据：

```text
item_id
```

判断是否首次出现。

---

## 11. 历史记录

`history.jsonl` 每次发现商品状态变化时追加。

例如：

```json
{
  "timestamp": "2026-08-15T18:20:00",
  "item_id": "123456789",
  "price": 1999,
  "title": "RTX4070...",
  "event": "discovered"
}
```

后续可能还有：

```text
detail_loaded
filter_passed
filter_rejected
order_success
order_unknown
order_failed
```

主要用于调试和审计。

---

## 12. 商品身份过滤

新增：

```text
identity_filter.py
```

执行顺序优先于损坏判断。

### 主要目的

判断该商品是不是：

> 当前关键词想买的商品本体。

例如搜索：

```text
RTX 4070
```

应排除：

```text
RTX4070 空盒
RTX4070 散热器
RTX4070 求购
RTX4070 租赁
RTX4070 定金
RTX4070 维修件
```

### 判断来源

组合：

```text
title
+
seller_desc
```

规则来源：

```text
全局 invalid_item_words.txt
+
monitor.exclude_words
```

命中后：

```text
直接 reject
```

---

## 13. 损坏 / 瑕疵过滤

`rule_filter.py` 处理：

```text
negative_words.txt
traction_words.txt
```

### 文本标准化

匹配之前先统一：

```text
Unicode normalize
大小写统一
全角 → 半角
去多余空格
连续特殊符号处理
```

主要用于减少：

```text
V X
v.x
微 信
```

等简单变体漏检。

### 否定判断

仍保留轻量否定判断，例如：

```text
无划痕
没有磕碰
未维修
```

但不尝试做复杂自然语言推理。

复杂表达交给 AI。

例如：

```text
没有明显划痕
只有正常使用痕迹
好坏不清楚
朋友的机器，不懂情况
```

规则层只提供初筛。

---

## 14. AI 过滤

AI 输入：

```text
monitor keyword
title
description
price
```

要求返回：

```json
{
  "damaged": false,
  "damaged_reason": "",
  "traction": false,
  "traction_reason": "",
  "wrong_item": false,
  "wrong_item_reason": ""
}
```

### AI 主要作用

辅助判断：

```text
复杂损坏描述
模糊商品状态
语义引流
商品本体错误
```

### AI 失败

如果：

```text
超时
HTTP错误
JSON解析失败
API key不存在
```

则：

```text
使用规则层结果
```

因为系统最终不会自动付款。

但邮件中必须明确显示：

```text
AI检查状态：失败 / 未执行
```

方便人工付款前加强检查。

---

## 15. Orderer 拍单决策

流程：

```text
新商品
↓
identity_filter 通过
↓
rule_filter 通过
↓
ai_filter 未明确拒绝
↓
monitor.auto_order == true
↓
price <= monitor.max_price
↓
无多规格
↓
item_id 不存在历史订单
↓
当天成功订单数 < daily_limit
↓
距离上一单 >= order_interval_minutes
↓
执行 browser.order
```

如果任意条件不满足：

```text
仅通知 / 记录
```

---

## 16. 防止重复拍单

这是 v5 的核心保护之一。

在真正执行：

```text
browser.order()
```

之前检查：

```text
orders.json
```

如果同一：

```text
item_id
```

已经存在：

```text
success
unknown
```

则：

```text
禁止再次提交订单
```

### 允许重新尝试的情况

只有明确：

```text
failed
```

并且失败原因属于安全可重试类型时，未来才可考虑人工决定是否重试。

v5 默认：

```text
自动失败不重试
```

---

## 17. `orders.json`

示例：

```json
[
  {
    "item_id": "123456789",
    "timestamp": "2026-08-15T18:30:00",
    "title": "RTX4070...",
    "price": 1999,
    "monitor": "rtx4070",
    "status": "success",
    "order_id": "20260815xxxx",
    "reason": null
  }
]
```

`status`：

```text
success
failed
unknown
```

---

## 18. UNKNOWN 订单处理

典型情况：

```text
已经点击提交订单
↓
页面超时
↓
程序不知道是否生成订单
```

此时：

```text
status = unknown
```

处理：

```text
1. 写入 orders.json
2. 标记 item_id 已存在订单尝试
3. 禁止程序再次自动拍该商品
4. 邮件通知：
   "订单状态无法确认，请人工检查闲鱼待付款订单"
```

不自动重复点击提交。

---

## 19. 邮件通知

主要通知类型：

### 19.1 新商品

包含：

```text
Monitor
关键词
标题
价格
链接
描述
过滤结果
AI检查状态
是否满足自动拍条件
```

### 19.2 拍单成功

包含：

```text
标题
价格
订单号
商品链接
AI检查状态
```

明确提醒：

```text
已生成待付款订单，请人工检查后决定是否付款。
```

### 19.3 多规格商品

```text
检测到多个规格
→ 未自动拍
→ 请人工打开查看
```

### 19.4 UNKNOWN

```text
订单提交后状态无法确认
→ 请检查闲鱼待付款列表
```

### 19.5 风控

```text
验证码
登录失效
页面异常
连续失败
```

---

## 20. 风控策略

默认：

```text
poll_interval_minutes = 5
random_delay_seconds = [3, 8]
daily_limit = 3
order_interval_minutes = 20
```

原则：

- 只打开新商品详情；
- 已见商品不重复进入详情页；
- 同一个商品不会因不同关键词重复处理；
- 不自动付款；
- 不绕过验证码；
- 触发风控立即暂停；
- 不连续快速拍单；
- 不无限重试。

---

## 21. Runtime 状态

新增：

```text
runtime_state.json
```

例如：

```json
{
  "paused_until": "2026-08-15T19:30:00",
  "pause_reason": "captcha"
}
```

这样程序即使：

```text
重启
崩溃
Windows 重启
```

也不会立即绕过原来的暂停时间。

---

## 22. 单实例运行

增加：

```text
data/run.lock
```

启动时：

```text
检查是否已有实例
↓
有
→ 直接退出并记录日志
```

防止：

```text
Windows 启动项启动了一份
+
用户手动又启动一份
```

导致重复搜索和重复拍单。

实现保持简单即可，不引入复杂依赖。

---

## 23. 异常调试现场

正常情况下不需要频繁截图。

但出现以下情况：

```text
选择器失效
详情读取失败
价格解析失败
验证码
提交订单状态 unknown
页面结构异常
```

自动保存：

```text
data/debug/
├── 20260815_183000_item_123.png
└── 20260815_183000_item_123.html
```

方便后续快速修复选择器。

---

## 24. 数据文件

| 文件 | 用途 |
|---|---|
| `storage_state.json` | Playwright 登录会话 |
| `seen.json` | 全局 item_id 去重 |
| `history.jsonl` | 商品与状态历史 |
| `orders.json` | 拍单记录 |
| `runtime_state.json` | 风控暂停状态 |
| `run.lock` | 防止重复运行 |
| `logs/*.log` | 运行日志 |
| `debug/*` | 异常页面现场 |

---

## 25. 错误处理

### 搜索失败

```text
当前 monitor 记录错误
→ 继续下一个 monitor
```

### 详情失败

```text
不自动拍
→ 记录
→ 可发送通知
```

### AI 失败

```text
降级规则判断
→ 邮件注明 AI 未检查
```

### 订单失败

```text
记录 failed
→ 不自动重试
```

### 订单状态未知

```text
记录 unknown
→ 禁止重复拍
→ 通知人工检查
```

### 验证码

```text
暂停
→ 持久化 paused_until
→ 邮件通知
```

---

## 26. 日志

使用标准库：

```python
logging
```

日志目录：

```text
data/logs/
```

按日期滚动。

关键事件至少记录：

```text
SEARCH_START
ITEM_DISCOVERED
ITEM_DUPLICATE
DETAIL_OK
DETAIL_FAILED
FILTER_REJECT
FILTER_PASS
AI_FAILED
ORDER_SKIP
ORDER_START
ORDER_SUCCESS
ORDER_FAILED
ORDER_UNKNOWN
CAPTCHA_DETECTED
LOGIN_EXPIRED
RUNTIME_PAUSED
```

---

## 27. 测试策略

### 纯逻辑单测

不依赖浏览器。

覆盖：

```text
identity_filter
rule_filter
全局 dedupe
monitor 独立价格阈值
orders 防重复
daily_limit
unknown 禁止重拍
```

### 浏览器冒烟测试

使用：

```text
headless=false
buy.enabled=false
```

先验证：

```text
搜索
排序
扫描边界
详情
规格检测
过滤
通知
```

### 拍单测试

正式开启前：

```text
daily_limit = 1
```

人工全程观察浏览器执行一次。

确认：

```text
成功生成待付款订单
+
程序没有触发任何支付动作
```

后再切换常驻运行。

---

## 28. 首次运行策略

这是 v5 新增的重要设计。

第一次运行时：

```text
seen.json 不存在
```

此时搜索到的商品不能准确判断是否为刚发布的新商品。

因此默认：

```text
首次运行
↓
每个 monitor 扫描最多 max_scan_items
↓
建立 seen 基线
↓
不执行自动拍单
↓
发送初始化完成通知
```

第二轮开始：

```text
只处理相对于基线真正出现的新商品
```

避免第一次启动直接产生大量待付款订单。

---

## 29. 后期扩展点

| 想改什么 | 改哪里 |
|---|---|
| 闲鱼页面改版 | `browser/selectors.py` |
| 搜索流程改版 | `browser/searcher.py` |
| 新增商品有效性规则 | `identity_filter.py` / `invalid_item_words.txt` |
| 某关键词特殊规则 | `monitors[].exclude_words` |
| 新增通知渠道 | 新建 `Notifier` |
| 更换 AI 服务 | `config.ai.base_url/model` |
| 修改价格阈值 | 当前 monitor 的 `max_price` |
| 新增过滤器 | 新建 `Filter` 子类 |
| 未来数据量变大 | JSON 迁移 SQLite |

---

## 30. 实施阶段

### 阶段 1：基础骨架

完成：

```text
目录
config
models
全局 dedupe
runtime
日志
词表
```

### 阶段 2：浏览器可行性验证

使用 `probe.py` 确认：

```text
是否需要登录
最新发布排序方式
商品卡片选择器
详情选择器
价格选择器
规格选择器
立即购买按钮
提交订单按钮
待付款状态识别
验证码识别
```

### 阶段 3：搜索与详情

实现：

```text
session
searcher
detail
首次基线
扫描到旧商品为止
```

### 阶段 4：过滤

实现：

```text
identity_filter
rule_filter
ai_filter
```

### 阶段 5：通知

实现：

```text
新商品通知
多规格通知
AI 异常
风控告警
```

### 阶段 6：拍单

实现：

```text
orderer
order
daily_limit
order_interval
success / failed / unknown
防重复订单
```

### 阶段 7：测试与常驻运行

完成：

```text
pytest
有头浏览器冒烟
一次人工观察拍单
Windows 常驻运行
```

---

## 31. v5 最重要的安全边界

### 边界 1

```text
程序可以提交订单
但永远不能付款
```

### 边界 2

```text
同一个 item_id 只能自动提交一次
```

### 边界 3

```text
提交订单后无法确认结果
→ UNKNOWN
→ 禁止再次提交
```

### 边界 4

```text
多规格
→ 默认不自动拍
```

### 边界 5

```text
第一次启动
→ 只建立基线
→ 不自动拍历史商品
```

### 边界 6

```text
验证码 / 风控
→ 停止
→ 不硬闯
```

---

## 32. 风险与注意事项

### 平台风险

自动化访问和频繁创建订单仍可能触发闲鱼风控。

本项目只能降低概率，不能保证账号永远不受限制。

### 待付款订单

自动拍下会：

```text
产生待付款订单
可能短期占用卖家库存
订单超时后自动关闭
```

因此仍应控制每日数量。

### 过滤误判

规则和 AI 都无法保证 100% 正确。

由于本系统不自动付款：

```text
最终商品判断
最终价格判断
最终卖家判断
最终交易判断
```

均由用户人工完成。

### 页面结构

闲鱼页面结构可能随时调整。

因此所有：

```text
selector
排序方式
登录判断
规格检测
订单成功判断
```

必须通过 `probe.py` 实测后固化。

### 密钥安全

以下文件不得进入 Git：

```text
config/search.json
config/account.json
config/order.json
data/storage_state.json
data/
```

SMTP 授权码和 AI API Key 仅保存在本地。

---

## 33. 最终设计总结

v5 的目标不是把系统做成复杂的全自动交易平台，而是保持：

```text
简单
可靠
容易维护
人工最终确认
```

核心流程：

```text
监控新品
↓
全局去重
↓
过滤错误商品
↓
读取详情
↓
价格判断
↓
无多规格
↓
自动创建待付款订单
↓
邮件通知
↓
人工确认
↓
人工付款 / 放弃
```

当前阶段继续使用：

```text
JSON + JSONL
```

即可。

只有未来出现：

```text
商品数量很大
需要后台管理
需要复杂查询
需要多进程
```

时，再考虑迁移 SQLite。
