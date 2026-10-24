# McDiff 演示页

这个目录就是「可分享 demo 页」：一个**单文件**静态页面 + 一份数据文件，零外链、零 CDN、零 `fetch`。

```
docs/
├── index.html   ← 演示页（手写，不会被 report 覆盖）
├── data.js      ← 数据（python -m mcdiff report 生成，别手改）
└── data.json    ← 同一份数据的 JSON 版，给脚本/调试用
```

## 怎么打开

**方式 1：直接双击（最快）**

双击 `docs/index.html` 即可，`file://` 下能正常渲染。页面用 `<script src="data.js">` 加载数据，
所以在 `file://` 下也能工作（不用 `fetch`，不受本地文件跨域限制）。

**方式 2：起个本地服务（推荐截图/分享前用）**

```bash
python -m http.server -d docs 8765
# 打开 http://127.0.0.1:8765/
```

**方式 3：GitHub Pages**

仓库 Settings → Pages → Source 选 `Deploy from a branch`，分支选默认分支、目录选 `/docs`，
保存后等一分钟即可访问 `https://<用户名>.github.io/<仓库名>/`。
页面没有构建步骤，也没有任何需要联网的资源。

## 数据从哪来

```bash
python -m mcdiff capture --store 1420576   # 采一次门店菜单快照（= 一次 commit）
python -m mcdiff report                    # 生成 docs/data.js + docs/data.json
```

页面里所有数字都来自 `data.js`，没有任何硬编码的业务数据：
首屏四个大数字取 `totals`，博物馆统计取 `museum.stats`，时间线节点取 `snapshots`，
墓碑取 `museum.items` 里 `status === "gone"` 的项。

**diff 是前端现场算的**：`detail` 里存了每个快照的完整菜单（`code → [name, price_cents, category, tags]`），
所以任意两个快照都能在浏览器里重新比较，规则与 `mcdiff/diff.py` 完全一致：

| 判断顺序 | 条件 | 结果 |
|---|---|---|
| 1 | 只在目标里 | `added` 新上 |
| 2 | 只在基线里 | `removed` 下架 |
| 3 | 名称不同 | `renamed` 改名 |
| 4 | 价格不同 | `price_up` / `price_down` |
| 5 | 标签集合不同 | `tags` 标签变化 |
| 6 | 分类不同 | `moved` 换分类 |

输出顺序固定为 `added → removed → price_up → price_down → renamed → tags → moved`（同类按 `code` 排序）。

## 页面上有什么

1. **首屏**：一句话标题、一行大数字（快照次数 / 追踪 SKU / 已下架 / 涨价次数），以及一个真的 `git diff` 代码块。
2. **事件卡**：把「一次采集里变化最大的那次」单独拎成一行
   （`⚡ 05:05 深夜 → 早餐 · 这家门店在 23 分钟里换掉了 64 个 SKU：巨无霸 下架，猪柳麦满分 上架`），
   右侧是 `+61 / -64` 角标和「看这组 diff」按钮，点一下就把两个下拉框切到这组。
3. **任意两次快照 diff**：两个下拉框选基线与目标，切换后实时重算。
   变化会先分桶，**默认只显示「实质变化」**，把餐段轮换折叠起来：

   | 桶 | 判定 | 显示 |
   |---|---|---|
   | 涨价 / 降价 / 改名 / 标签 / 分类 | 引擎直接给出 | 逐行显示 |
   | **永久下架** | 该 SKU 在 `museum.items` 里 `status == "gone"`（= 它出现过的每个餐段的最新快照都不再上架） | 逐行显示（`-N 永久下架`） |
   | **真新上架** | 同一餐段内、本轮才第一次出现的 SKU | 逐行显示（`+N 新上架`） |
   | **餐段轮换** | 基线/目标属于不同餐段（早/午/深夜切换）时的整批上/下架，或同餐段内「暂离又回来」 | 折叠成 `- N 个 SKU 随餐段让位` / `+ N 个 SKU 随餐段上架` 两行，点「展开」或点 chips 里的 `⇄ N 餐段轮换` 才逐条显示 |

   这样「深夜 → 早餐」那种一次换掉 125 个 SKU 的对比不会刷屏，chips 也会老实写成
   `+0 新上架 / -0 永久下架 / ↑2 涨价 / ↓1 降价 / ⇄4 餐段轮换`，底部 `@@` 行同时给出
   `3 处实质变化（另有 4 处餐段轮换）· 不同餐段：午餐 → 深夜`。
   不同餐段时 chips 会多出一个「只看同餐段」按钮，一键把基线换成**同餐段的上一次快照**（真正的同日对比）。
   `@@` 行里的 `+N -N` 用分桶后的数字，和控制台里看得到的行一致。

   默认 pair「可读性优先」：优先用 `diffs[0]`；如果那组是「整本菜单被餐段切换换掉」（改动量 > 目标 SKU 数的 35%），
   就退回到「有价格 / 改名等实质变化、改动量适中」的那组。
   展开餐段轮换后如果仍超过 40 行，先渲染 30 行 + 「展开全部 N 处变化」（`COLLAPSE_THRESHOLD` / `COLLAPSE_SHOW`）。
4. **时间线**：横向排开全部快照节点，颜色深浅表示相对上一次的变化数量；
   点节点设为目标、`Shift` 点击或再次点击设为基线（键盘 `Enter`/`Space` 同样可用，`Shift+Enter` 设为基线）。
5. **麦门考古博物馆**：每个已下架 SKU 一块墓碑，显示在架天数、最后价格、最后一次出现日期和墓志铭；
   超过 12 块时先给最近的 12 块，其余用「展开全部 N 块墓碑」按钮展开。
6. **它是怎么做的**：MCP 采集 → 快照 commit → diff/博物馆 三步，列出真实用到的 MCP tool 名与采集命令。
7. **数据水印**：只要 `totals.synthetic` 为真，页面顶部固定一条提示，说明含示例快照。
8. **页脚**：参赛声明（非麦当劳官方产品）、数据来源说明、MIT、仓库链接。

## 配色（白底 + 麦当劳红金）

固定浅色主题（`color-scheme: light`，不跟随系统深色）。所有颜色都是 `index.html` 顶部 `:root` 里的变量，改一处即可换色：

| 变量 | 值 | 用途 |
|---|---|---|
| `--bg` / `--bg-soft` / `--bg-alt` | `#FFFFFF` / `#FFFBF3` / `#FAFAFA` | 页面底 / 博物馆等斑马区 / 页脚 |
| `--line` / `--line-soft` | `#E7E1D6` / `#F0EBE2` | 卡片描边 / 分隔线 |
| `--fg` / `--fg-dim` / `--fg-mute` | `#1A1A1A` / `#5C5C5C` / `#767676` | 主文字 / 次文字 / 元信息（白底 4.5:1+） |
| `--fg-faint` | `#6F6F6F` | 行号、时间线标签（比灰色 `#8A8A8A` 深一档，保证 ≥4.5:1） |
| `--red` / `--red-ink` / `--red-dark` | `#DA291C` / `#C8102E` / `#B71C1C` | 品牌红 / 正文红字 / 按钮 hover |
| `--gold` / `--gold-wash` / `--gold-wash-2` | `#FFC72C` / `#FFF4CC` / `#FFF8E1` | 强调 / 事件卡底 / 水印条底 |
| `--gold-brown` / `--brown-ink` | `#B07A00` / `#6B4E00` | 大号金色数字（仅 ≥26px）/ 金底上的字 |
| `--add` `--add-bg` | `#1B7A38` `#EDF8F0` | diff `+` 行 |
| `--del` `--del-bg` | `#C62828` `#FDECEC` | diff `-` 行 |
| `--mod` `--mod-bg` | `#8A5A00` `#FFF7E3` | diff `~` 行 |

金色只做点缀（顶部双色条、事件卡、徽章、时间线示例节点、H1 马克笔），大面积底色仍是白 / 暖白。

## 需要改的地方

- `data.js` 里的 `project.repo` 由 `mcdiff/report.py` 写入，现在是真实仓库地址
  `https://github.com/Chendykjx/mcdiff`；页脚 / 导航的 GitHub 链接直接读它，不用改 HTML。
- 页面本身不含任何外部链接、字体或图片；唯一的 `http://` 字符串是 SVG 的 XML 命名空间标识符
  `http://www.w3.org/2000/svg`，它不是网络请求，不会产生 404。
- 分享预览走 `media/og.png`（`<meta property="og:image">`，相对路径）：只给微信 / Twitter / Slack 这类爬虫读，
  浏览器打开页面时不会请求它。`media/demo.gif` 是给 README 用的动图，页面不引用。

## 兼容性

- 现代浏览器（Chrome / Edge / Safari / Firefox）直接可看。
- 手机宽度下 diff 区块在自己的框里横向滚动，页面不会横向溢出。
- 全部图形（金拱门、墓碑、favicon）都是内联 SVG，不加载任何图片文件。
