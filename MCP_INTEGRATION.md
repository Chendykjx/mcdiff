# MCP 集成说明

本项目真实调用麦当劳中国 MCP Server。本文记录**实际用到的 Server、Tool、调用时序、数据契约与踩坑**，全部基于 2026-10-24 ~ 10-25 的真实请求。

## 1. 接入信息

| 项 | 值 |
|---|---|
| Server | 麦当劳中国 MCP（`https://mcp.mcd.cn`） |
| 协议 | Streamable HTTP + JSON-RPC 2.0 |
| 认证 | `Authorization: Bearer ${MCD_MCP_TOKEN}`，Token 仅从环境变量读取，仓库内无真实凭证 |
| 协议版本 | 服务端返回 `2025-03-26`，响应体使用 `structuredContent`（2025-06-18 特性） |
| 限流 | 600 次/分钟；客户端最小请求间隔 0.11s（≈545/min），429 指数退避重试 |
| 实测 | `initialize` → `serverInfo={name:"mcd-mcp", version:"1.0.0"}`；`tools/list` → 35 个工具 |

## 2. 使用的 Tool 清单

| Tool | 本项目用途 | 只读 | 关键入参 | 关键出参 |
|---|---|---|---|---|
| `query-meals` | 拉取门店菜单，作为快照主体 | ✅ | `storeCode`、`orderType`、`beType`（外送/得来速还需 `beCode`） | `categories[].meals[].code/tags`、`meals{code}.name/currentPrice/originalPrice/discountType` |
| `calculate-price` | 对指定商品做官方核价，校验菜单展示价 | ✅ | `storeCode`、`orderType`、`beType`、`items[{productCode,quantity}]` | `price`（**整数分**）、`productList`、`takeWayList` |
| `query-store-coupons` | 记录当前门店可用券（只存标题与有效期，不存券码） | ✅ | `storeCode`、`orderType`、`beType` | `title`、`tradeDateTime`、`products` |
| `query-promotions` | 记录门店满减/折扣规则，供「券门槛变化」追踪 | ✅ | `storeCode`、`orderType`、`beType` | `promotionType`、`ruleCategory`、`ruleDetail.orderDiscount` |
| `query-nearby-stores` | 按城市 + 关键词找店，挑营业中的门店 | ✅ | `beType`、`searchType`、`city`、`keyword` | `storeCode`、`businessStatus`、营业时间、`reservation` |
| `now-time-info` | 时间基准，用于餐段判定与快照时间戳 | ✅ | — | `date`、`datetime`、`dayOfWeek` |

> 35 个工具里还有 `create-order`、`auto-bind-coupons`、`draw-lottery` 等**写操作**工具，本项目**不调用**，仅在文档中标注风险。

## 3. 调用时序（一次采集）

```
python -m mcdiff capture --store 1420576
  │
  ├─→ (可选) query-nearby-stores {beType:1, searchType:2, city:"深圳市", keyword:"麦当劳"}
  │        └─ 找到 storeCode 时补齐门店名/营业时间；找不到就用最小信息继续
  │
  ├─→ query-meals {storeCode, orderType:1, beType:1}
  │        └─ 解析 categories + meals → 归一化为 {code: {name, price_cents, tags, category, ...}}
  │        └─ 计算 checksum = sha256(meals + categories)
  │
  ├─→ query-store-coupons ┐ 失败不致命（打烊门店会返回 600057）
  ├─→ query-promotions     ┘ 记录到 extras，同时保留错误信息
  │
  ├─→ (可选) calculate-price {items:[{productCode, quantity:1}]}
  │        └─ 记录 price（分）与 productList 长度，用于识别「静默丢项」
  │
  └─→ 落盘 data/snapshots/<storeCode>/<YYYYMMDDTHHMM>-<daypart>.json
           └─ 与上一份快照 checksum 相同则跳过写入（避免无意义的 commit）
```

`diff` / `museum` / `report` 全部是**纯本地计算**，不再请求 MCP。

## 4. 数据契约与已踩的坑（真做过才写得出来）

1. **响应是双通道**：`result.content[0].text` 是给 LLM 看的 Markdown（里面甚至写着「必须完整展示 storeCode」这类展示指令），`result.structuredContent` 才是机器可读 JSON（`success/code/message/datetime/traceId/data`）。**解析必须走 `structuredContent`。**
2. **业务错误是 HTTP 200**：只有鉴权失败才 401/403；打烊门店（600057）、参数缺失（600058）都是 HTTP 200 + `success:false` + `code`。`result.isError` 恒为 `false`，**不能用来判断失败**。
3. **无状态短连接**：`initialize` 响应没有 `Mcp-Session-Id`，`Connection: close`，`Content-Type: application/json`（不是 SSE）。客户端仍实现了 SSE 分支（多行 `data:` 拼接），以防服务端切换传输方式。
4. **金额双单位**：`calculate-price` 返回**分**（2300 = ¥23.00），`query-meals` 的 `currentPrice` 是**元字符串**（"14.9"）。本项目内部统一用整数分，只在校验和与展示时转换。
5. **非法 productCode 会被静默丢弃**：传 `1120` + `3050`（后者只存在于套餐选项里），`productList` 只剩 1120，**不报错也不提示**。因此核价后必须校验 `len(productList)` 与输入一致，否则少算钱。
6. **门店必须在营业时间**：22:42 查询一家 22:00 打烊的门店，`query-meals` 直接返回 `600057 门店可能已关闭或不在营业时间`。快照器会打印跳过原因，不写入空数据。
7. **门店搜索必须 city + keyword**：只给 `city` 会返回 `600058 城市名或者关键词不能为空`；`searchType=1` 依赖「收藏餐厅」，新账号为空（`600050`）。
8. **菜单按 code 索引，营养表按名称索引**：`list-nutrition-foods` 的 160 行数据以「餐品名称」为键，而菜单以 `productCode` 为键；本项目 v0.1 不打通两者（避免错误匹配），只在博物馆展示菜单自身字段。
9. **响应体积**：`campaign-calendar` 一次约 69KB、28 个事件且同一活动多日重复；菜单响应约 100KB。快照只保留必要字段（约 36KB/次），并跳过无变化写入。

## 5. 业务价值与边界

- **解决什么**：菜单价格与供给的变化没有公开历史，McDiff 用官方 MCP 把它变成可 diff、可追溯的时间序列。
- **为什么必须用 MCP**：价格、券、门店营业状态都是实时的，离线写死没有意义；只有官方接口能给出 `storeCode` 级菜单与官方核价。
- **非目标**：不做自动下单、不代领券、不抽奖、不保存 Token、不采集个人信息、不提供医疗或营养建议。
- **合规**：仅使用只读工具；示例数据强制标记 `synthetic`；文案只描述「菜单变化」，不评价品牌与友商。
