# 5 分钟 Demo 讲解稿

## 演示前准备

```powershell
Copy-Item .env.example .env
# 在 .env 中填写 DASHSCOPE_API_KEY；没有 Key 也能演示规则降级和安全链路
docker compose up -d --build --wait
```

打开 <http://localhost:5173>，确认右下角为“服务正常”，当前用户使用 `demo-user`。准备上传 `demo/day10-knowledge.md`，权限 subjects 填 `demo-user`。为避免等待向量化，演示检索时可先选 keyword；配置模型 Key 后可展示 hybrid + rerank。

## 0:00–0:35：一句话定位

“这是一个可运行的企业知识库 Agent。它不只会回答问题，还把文档入库、权限过滤、记忆、工具调用、人工审批、Docker 沙箱、Trace 和 Eval 串成了一条可以审计的链路。”

在总览页快速指出六个模块：Agent、审批、长期记忆、知识库、检索实验室和系统总览。

## 0:35–1:25：文档入库与权限

进入“知识库”，上传 `demo/day10-knowledge.md`：

- tenant：`default`
- workspace：`default`
- tags：`day10,demo`
- permissions subjects：`demo-user`

展开文档详情，指出 source hash 去重、解析器、chunk、embedding job 和状态。补一句：“重复上传不会创建第二份文档；同一幂等键会原样重放第一次响应。”

## 1:25–2:10：检索与权限过滤

进入“检索实验室”，用 keyword 查询：

> 发布验收必须满足什么条件？

展示文档名、chunk、标题、得分和 metadata。切换成另一无权限主体后解释：私有 chunk 在 SQL 查询阶段就被过滤，不会先取回再由模型判断，因此不会泄漏到 prompt。

## 2:10–3:05：Agent 回答、引用与 Trace

进入“智能体”，strategy 选 keyword，提问：

> 根据文档说明发布验收必须满足哪些条件？

展示最终回答中的 `[C1]`、引用卡片、routing decision、tool action 和 trace。强调：“模型写出的 citation 必须来自本轮 catalog；如果模型编造 `[C999]`，Eval 会把运行转到 `escalated_to_human`，不会把无来源答案伪装成高置信结果。”

## 3:05–4:05：高风险审批与幂等

提问：

> 给 alice 发送消息：Day10 演示通过

页面进入 `waiting_approval`。切到“审批台”，展开 action，展示风险等级、原始用户授权证据和关联 trace。点击批准或拒绝，说明审批请求必须带 `Idempotency-Key`；重复点击只返回第一次结果，不会重复写 Outbox 或重复执行。

## 4:05–4:40：沙箱边界

安全命令：

> 请在 Docker 沙箱执行命令 argv: ["python","-c","print(6*7)"]

展示 stdout、资源限制和 `container_removed=true`。随后说明回归测试还会用 `rm -rf /` 验证拒绝列表，并验证容器无网络、只读根文件系统、非 root、受 CPU/内存/PID/超时限制。

## 4:40–5:00：收尾

“这个项目的重点不是再包一层聊天 UI，而是把 Agent 的不确定性放进确定性的工程边界：数据权限在检索层，副作用在工具层，危险执行在沙箱层，人工决策有幂等保护，最后所有路径落到 Trace 和 Eval。完整验收可以用一条脚本稳定复现。”

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\day10_verify.ps1
```
