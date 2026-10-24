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

1. **首屏**：一句话标题、一行大数字（快照次数 / 追踪 SKU / 已下架 / 涨价次数），以及一个真的 `git diff` 代码块
   （默认展示 `diffs[0]`，即变化最多的那组：10-24 午餐 → 10-25 深夜）。
2. **任意两次快照 diff**：两个下拉框选基线与目标，切换后实时重算；上方是 `+N / -N / ↑N / ↓N / 共 N 个 SKU` 的 chips。
3. **时间线**：横向排开全部快照节点，颜色深浅表示相对上一次的变化数量；
   点节点设为目标、`Shift` 点击或再次点击设为基线（键盘 `Enter`/`Space` 同样可用，`Shift+Enter` 设为基线）。
4. **麦门考古博物馆**：每个已下架 SKU 一块墓碑，显示在架天数、最后价格、最后一次出现日期和墓志铭。
5. **它是怎么做的**：MCP 采集 → 快照 commit → diff/博物馆 三步，列出真实用到的 MCP tool 名与采集命令。
6. **数据水印**：只要 `totals.synthetic` 为真，页面顶部固定一条提示，说明含示例快照。
7. **页脚**：参赛声明（非麦当劳官方产品）、数据来源说明、MIT、仓库链接。

## 需要改的地方

- `data.js` 里的 `project.repo` 目前是占位符 `https://github.com/你的账号/mcdiff`
  （由 `mcdiff/report.py` 生成），发布前替换成真实仓库地址，页脚的 GitHub 链接会自动跟着变。
- 页面本身不含任何外部链接、字体或图片；唯一的 `http://` 字符串是 SVG 的 XML 命名空间标识符
  `http://www.w3.org/2000/svg`，它不是网络请求，不会产生 404。

## 兼容性

- 现代浏览器（Chrome / Edge / Safari / Firefox）直接可看。
- 手机宽度下 diff 区块在自己的框里横向滚动，页面不会横向溢出。
- 全部图形（金拱门、墓碑、favicon）都是内联 SVG，不加载任何图片文件。
