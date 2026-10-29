# 拾歌代码架构地图

这是独立、只读的静态文档页面，不调用拾歌 API、不读取 `var/`、模型、录播或实际账户配置。浏览器中的计划勾选只保存在本机站点偏好中，不会自动重构代码。

## 打开

在仓库根目录：

```sh
python3 -m http.server 8778 --bind 127.0.0.1 --directory docs/architecture
```

打开 `http://127.0.0.1:8778/`。也可以直接用浏览器打开此目录的 `index.html`；页面不使用 fetch、CDN或外部字体，核心浏览可离线使用。完整源码链接需要连接 GitHub。

- **架构总览**：选择模块，查看职责、直接关联和源码入口；下面可筛选细分模块。
- **功能链路**：查看实际 UI → HTTP → 服务 → 任务 → 执行／存储步骤。
- **文件地图**：搜索中文功能、路径、类或函数名；展开符号查看源码片段。
- **重构规划**：明确区分建议类与已有代码，勾选后导出 JSON 计划。
- **部署与数据**：切换本地、Linux、Tauri，查看进程与数据归属。

## 事实与建议的边界

当前是 React + TypeScript，并没有 Vue。建议是让 React／未来 Vue 组件只承担渲染、状态绑定与事件转发，将有状态用例抽到独立 TS 服务类；时间换算、校验等无状态规则继续复用纯函数。训练、推理、媒体处理、权限和最终校验仍在 Python 服务中。

节点按逻辑职责分组，可能把同一文件拆成多项，也可能聚合多个相关文件；节点数不等于独立服务或包的数量。物理位置以源码链接为准。

“已实现”表示当前源码中有实现，不代表旧安装包或某台服务器已经部署此版本。接口预留和重构建议单独标记。模块边是人工审阅的调用／依赖关系，不是声称涵盖全部动态调用的自动调用图。

## 页面代码本身的边界

| 文件 | 职责 |
| --- | --- |
| `src/model.ts` | 数据契约、ArchitectureRepository 查询、ExplorerStore 导航／计划状态 |
| `src/view.ts` | ArchitectureView 呈现快照、图与源码详情 |
| `src/main.ts` | AtlasController 绑定事件、调用仓库与状态，不承担业务工程的实现 |
| `content.json` | 人工审阅后的模块、关系、功能与重构建议 |
| `evidence/` | 前端、后端、交付审阅记录 |
| `snapshot.py` | Python AST符号、类字段和路由提取，不导入服务模块 |
| `../../scripts/build_architecture.mjs` | 读取Git源码快照、提取TS AST、校验锚点并打包 |
| `data.js` / `app.js` | 浏览器用的生成文件 |

## 更新

先更新业务源码并提交，再审阅 `content.json` 的职责、调用关系、功能状态和建议。文件／符号自动索引不能替代语义审阅。`assemble.py` 可由 `evidence/` 重建人工内容模板；手工调整 content 后不要无意重跑覆盖。

已安装网页开发依赖后：

```sh
node scripts/build_architecture.mjs 2026-10-29
apps/web/node_modules/.bin/tsc --noEmit --strict --target ES2022 --module ESNext --moduleResolution bundler --lib ES2022,DOM,DOM.Iterable docs/architecture/src/main.ts
node --test docs/architecture/tests/*.test.mjs
```

日期参数表示本次审阅日期，请按实际日期更新。生成器读取Git中的源码；业务源码未提交时拒绝生成精确链接。每个源码链接固定到该次提交，符号行号由AST定位（配置／手工说明锚点只校验范围）。

页面内容与工具随Git版本保存；不应只重跑生成器就宣称所有旧建议已经重新审阅。
