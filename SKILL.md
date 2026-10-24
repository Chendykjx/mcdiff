---
name: mcdiff
description: 追踪麦当劳菜单的变化——采集门店菜单快照、diff 出今天谁涨价/降价/下架/新上、把消失的餐品收进「麦门考古博物馆」。当用户问「麦当劳菜单变了什么 / 哪些涨价了 / 下架了什么 / 价格历史 / 帮我记一下今天的菜单」时使用。
license: MIT
---

# McDiff

给麦当劳菜单建一个 git 仓库：一次采集 = 一次 commit，两次快照 = 一次 diff，下架的 SKU = 一块墓碑。

## 什么时候用

- 用户想知道「最近麦当劳菜单有什么变化 / 涨价了没 / 下架了什么」
- 用户想留档某家门店此刻的菜单与价格
- 用户想看某个餐品的价格历史、在架天数
- 用户要做「价格监控 / 菜单变更日志」类任务

## 前置条件

需要一个麦当劳 MCP Token：<https://open.mcd.cn/mcp> → 手机号登录 → 控制台 → 激活 → 复制，写入环境变量 `MCD_MCP_TOKEN`。

## 怎么用

```bash
# 0) 自检（Token、连通性、工具数量）
python -m mcdiff doctor

# 1) 找门店（city 与 keyword 必须同时给，否则接口返回 600058）
python -m mcdiff stores --city 深圳市 --keyword 麦当劳

# 2) 采集快照（营业中的门店才能查菜单，打烊会返回 600057）
python -m mcdiff capture --store 1420576 --city 深圳市 --keyword 麦当劳

# 3) 看最近两次的 diff（默认最后两次）
python -m mcdiff diff            # git 风格文本
python -m mcdiff diff --json     # 机器可读
python -m mcdiff diff --markdown # 表格

# 4) 博物馆：所有已下架的餐品
python -m mcdiff museum

# 5) 把两组快照导出成 GIF（终端打字 + 结论卡）
python -m mcdiff gif --out menu-diff.gif

# 6) 导出静态演示数据（给 docs/index.html）
python -m mcdiff report
```

## 输出约定

- 价格单位：内部一律**整数分**（1490 = ¥14.90）；展示时用 `format_cents`。
- 变更类型：`added` / `removed` / `price_up` / `price_down` / `renamed` / `tags` / `moved`。
- 快照路径：`data/snapshots/<storeCode>/<YYYYMMDDTHHMM>-<daypart>.json`，不可变、带 `checksum`。
- 示例数据：`seed` 生成的历史全部带 `"synthetic": true`，回答用户时必须说明这是示例。

## 安全边界

- 只读：本项目只调用 `query-meals`、`calculate-price`、`query-store-coupons`、`query-promotions`、`query-nearby-stores`、`now-time-info`。
- **不要**调用下单、领券、抽奖类工具（`create-order`、`auto-bind-coupons`、`draw-lottery` 等）。
- 不要把 Token、地址、手机号、积分写进任何输出或文件。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `600057 门店可能已关闭或不在营业时间` | 门店打烊 | 换 24h 店，或等到营业时段 |
| `600058 城市名或者关键词不能为空` | 只给了 `city` | `city` + `keyword` 同时给 |
| 核价少算钱 | 非法 `productCode` 被静默丢弃 | 校验 `productList` 长度 |
| diff 里几十个「新品」 | 其实是餐段切换（早/午/夜菜单不同） | 用 `diff --buckets` 或页面上的「餐段轮换」分组 |
| 营养数据对不上 | 营养表按名称、菜单按 code | 按名称归一化，缺就明说 |
