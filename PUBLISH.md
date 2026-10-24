# 发布清单（内部文档，发布后可删）

> 面向：仓库所有者。目标是在 **2026-10-25 23:59** 前把项目变成「公开仓库 + 报名 Issue」。

## 0. 前置检查

```bash
cd D:\my_data\VibeCoding\mcdiff
python -m unittest discover -s tests          # 61 个测试必须全绿
python -m mcdiff report                       # docs/data.js 与首页数据同步
grep -rn "MCD_MCP_TOKEN=" --include="*.json" .   # 仓库里不能出现真实 token
```

## 1. 改三处占位符

| 文件 | 替换 |
|---|---|
| `README.md` | `https://Chendykjx.github.io/mcdiff/` → 你的 Pages 地址 |
| `pyproject.toml` | `Homepage = "https://github.com/Chendykjx/mcdiff"` |
| `docs/index.html` | 页脚 GitHub 链接（如果页面里写死了占位符） |

## 2. 建仓库并推送

```bash
# 方式 A：gh 已登录
gh repo create mcdiff --public --source=. --remote=origin --push

# 方式 B：先在 GitHub 网页建空仓库 mcdiff（不要勾 README），再
git add -A
git commit -m "feat: McDiff v0.1.0 — 给麦当劳菜单建一个 git 仓库"
git branch -M main
git remote add origin https://github.com/<Chendykjx>/mcdiff.git
git push -u origin main
```

## 3. 打开 GitHub Pages

Settings → Pages → Source: `Deploy from a branch` → Branch: `main` / folder: `/docs` → Save。
等 1–2 分钟，访问 `https://<Chendykjx>.github.io/mcdiff/` 确认演示页可用。

## 4. 配置定时采集（可选，但很加分）

Settings → Secrets and variables → Actions：
- Secret `MCD_MCP_TOKEN`：你的 MCP Token
- Variable `MCD_STORES`：`1420576`（可多个，逗号分隔）
- Variable `MCD_CITY`：`深圳市`
- Variable `MCD_KEYWORD`：`麦当劳`

然后 Actions → snapshot → Run workflow 手动跑一次，确认能自动 commit。

## 5. 提交报名 Issue

到 https://github.com/M-China/mcd-developer-innovation-challenge/issues/new 粘贴（**四行、全角冒号、无空行、裸 URL**）：

```
【参赛申请】
项目名称：McDiff
项目地址：https://github.com/<Chendykjx>/mcdiff
项目简介：每天给麦当劳菜单打一次 commit，git diff 出今天谁涨价、谁下架；消失的餐品进"麦门考古博物馆"。
```

提交后等官方机器人回复「成功参赛」。没回或失败，检查：仓库是否 Public、是否缺文件、格式是否被换了行。

## 6. 冲 Star（比代码更重要的一半）

- 发即刻 / 小红书 / V2EX / 掘金，文案带那句一句话 + GIF + demo 链接；
- 找 5–10 个真实朋友 Star（规则禁止机器/多账号，不禁止真实用户认可）；
- 截止前 2 小时确认 Star ≥ 52（进榜线），截图存档。
