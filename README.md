# 🤖 灵爪备份 (openclaw-lingzhua)

灵爪（openclaw agent）的关键文件备份仓库。

## 目录结构

```
soul/           - 核心身份文件（SOUL.md、MEMORY.md、USER.md、AGENTS.md 等）
scripts/        - 交易和监控脚本
skills/         - 核心skills
```

## 恢复方法

如果灵爪崩溃需要恢复，从 GitHub 拉取后：
1. 将 `soul/` 下的文件放回 `~/.openclaw/workspace/`
2. 将 `scripts/` 下的文件放回 `~/.openclaw/workspace/scripts/`
3. 将 `skills/` 下的文件放回 `~/.openclaw/workspace/skills/`

---
> ⚠️ 本仓库仅含关键文件，不含大文件、图片、日志、数据库等。
> 最后更新：2026-04-28
