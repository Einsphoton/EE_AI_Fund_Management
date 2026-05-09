# Portfolio OCR Skill

把"持仓页截图"识别成 EE_AI_Fund_Management App 可导入的 JSON 文件。

## 我该看哪个文件？

- 想知道**怎么用** → 看 [`SKILL.md`](./SKILL.md)
- 想知道**JSON 格式** → 看 [`schema.json`](./schema.json) 或 [`example_output.json`](./example_output.json)
- 想**校验自己生成的 JSON** → 跑 `python validate.py your.json`

## 快速三步走

1. 把 [`SKILL.md` 里的 System Prompt](./SKILL.md#三system-prompt可直接复制) 粘到任意多模态 ChatBot
2. 上传持仓截图，把模型返回的 JSON 保存为 `.json` 文件
3. App → OCR 批量导入页 → **导入 JSON 文件** → 选你刚保存的 JSON → 对账确认入库

## 为什么要做成 Skill？

App 内置 OCR 和这个 Skill 共用 schema，但 Skill 有几个独占场景：

- 内置 OCR 限流（Kimi 免费档 3 RPM 等）时，用 Skill 在网页 ChatBot 里手动跑更快
- 想用 App 没接入的模型（GPT-4V / Claude Sonnet / 本地 Qwen-VL）做 OCR
- 想离线/隔离环境跑 OCR，再把 JSON 拷过来导入

## 文件清单

```
portfolio-ocr/
├── SKILL.md           # 主文档（含 System Prompt、Schema 说明、使用步骤）
├── schema.json        # JSON Schema 形式化定义
├── example_output.json # 一份合法的产物示例
├── validate.py        # 离线校验脚本（仅用标准库）
└── README.md          # 你正在看的文件
```
