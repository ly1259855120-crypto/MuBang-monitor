# *ST沐邦（603398）公告邮件监控

Python + GitHub Actions + 上交所主源 + 巨潮备用源 + SMTP。默认每10分钟检查一次，全天运行，无需购买服务器；AI默认关闭。证券代码固定603398，公司简称只作邮件标签。

## 最简部署（约10分钟）

1. 在 GitHub 创建一个空仓库，把**本目录内的文件**上传到默认分支根目录。确认能看到 `.github/workflows/monitor.yml`，不要多套一层 `mubang-monitor/`。网页上传时隐藏目录可能遗漏，可以在 GitHub 的 Add file → Create new file 中按该路径创建两个 workflow 并复制内容。
2. 在邮箱设置中开启 SMTP，取得“授权码/应用密码”。在仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret** 按下表添加配置。
3. 在 **Settings → Actions → General → Workflow permissions** 允许 **Read and write permissions**。若组织策略限制写入，需要仓库管理员放行。程序使用仓库自带 `GITHUB_TOKEN`，无需另建个人令牌。
4. 打开 **Actions → Mubang announcement monitor → Run workflow**。确认运行结果，并收到一封心跳邮件。第一次运行还会发送回溯窗口内的已有公告，不会默默建立基线而不通知。
5. 后续自动运行。默认北京时间23点以后第一次实际运行发心跳，次日第一次运行补发上一日完整日报。垃圾邮件文件夹也检查一下，并把发件地址加入白名单。

| Secret | 示例 / 含义 |
|---|---|
| `SMTP_HOST` | `smtp.qq.com`，按邮箱服务商填写 |
| `SMTP_PORT` | `465`，或 STARTTLS 的 `587` |
| `SMTP_USER` | 完整发件邮箱地址 |
| `SMTP_PASSWORD` | 邮箱SMTP授权码/应用密码，不是普通登录密码 |
| `MAIL_TO` | 收件地址，多地址用英文逗号分隔 |
| `MAIL_FROM` | 发件地址，可省略，默认等于 `SMTP_USER` |

实际上必填5项：`MAIL_FROM`可省略，端口也有默认值。不要把真实邮箱密码放进代码、README、Issue、聊天或提交记录。所有配置示例都是占位符。

可选的 **Variables**（不是 Secrets）：

| Variable | 默认值 | 用途 |
|---|---|---|
| `SMTP_SECURITY` | `ssl` | 也支持 `starttls`，不支持明文登录 |
| `ENABLE_BACKUP` | `true` | 巨潮交叉校验；设 `false` 关闭 |
| `HEARTBEAT_HOUR` | `23` | 北京时间0–23，定时心跳生成小时 |
| `CNINFO_ORG_ID` | 自动查询 | 接口变化时可手动配置，当前实测为 `9900024444` |
| `ENABLE_AI` | `false` | 只有明确设为 `true` 才调用付费模型 |

定时表达式默认 `3-59/10 * * * *`（UTC，每小时03、13、23、33、43、53分）。需要5分钟可改成 `3-59/5 * * * *`。错开整点只能减少部分拥挤，不保证准点。

## 可靠性边界：必须先知道

- **GitHub Actions不是准点调度服务。** 官方说明高负载时可能延迟，甚至丢弃排队任务。公开仓库60天无仓库活动时定时任务可能停用；定时工作流只在默认分支运行。不能沿用“GitHub Actions稳定性很高、不会停”的说法。[GitHub定时规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- 每次查询现在往前48小时；源只给日期时，保留边界日期全天，因此实际可能多查不足24小时。恢复运行后发现距上次检查超过30分钟会告警。停机超过回溯窗口仍可能漏掉更早公告。
- **完全没有启动的程序无法给自己发异常邮件。** 心跳缺失需要人工检查；要无人值守地发现整体停摆，可另配独立的失联监测服务，或用自己常在线机器的系统调度器运行本项目。本包没有冒充一个独立看门狗。
- SMTP故障时，异常邮件同样可能发不出去；此时工作流标红，队列保留重试。开启GitHub Actions失败通知，并检查邮箱服务商额度/限制。
- SMTP成功意味着服务器接受，不等于收件箱保证送达。发送成功到状态推送之间若崩溃，下次可能重复提醒，这是“至少一次尝试”取舍；不承诺严格恰好一次。固定Message-ID可辅助邮件客户端识别重复，但不是保证。
- 网站查询接口是网页后端，非有SLA的商业数据服务，可能被限流、封锁或改版。GitHub境外运行器到国内网站的网络可用性要以部署后的运行结果为准。

## 做了什么

1. SSE、CNINFO各自模块化实现`fetch(code, start, end)`，分页读完；状态码、JSON、关键字段、证券代码、URL域名和分页异常都显式报错。网络重试2次，连接/读取超时分别10/30秒。
2. 每轮先抓取并持久化待发送通知，再发送原始公告邮件；某源异常不阻断另一源。备用源独有公告也发原始提醒，并额外标记“主源本轮未确认”。源差异不自动等同漏披露，也可能是同步延迟或标题格式差异。
3. SQLite记录规范化URL、标题、日期、披露公告编号（源提供时）、来源记录ID。**来源记录ID不冒充公司公告编号。** SSE未返回编号且标题不含编号时明确写“未提供”，原始提醒不会等待PDF解析。
4. 同源规范化URL去重，跨源以证券代码+规范化标题+日期+兼容的公告编号归并。编号缺失时允许标题日期匹配。来源记录ID保留审计；同ID/同标题但PDF URL改变，保守当成新版本提醒。仅有标题相同不会跨日期去重；标题格式差异宁可多发。不能检测服务器在同一个URL下无提示替换PDF内容。
5. 新公告和待发送邮件在同一事务提交；SMTP成功后才更新发送标记。失败保留到下次。两源正常但0条公告是正常状态；结构损坏绝不当0条。
6. 首次出现抓取故障立即排队异常邮件；同类源故障每天最多一封，恢复后发恢复通知。备用源差异每个公告通知一次。持久化失败、解释器启动失败等进程外故障以Actions失败记录为准。
7. 每日心跳提供北京时间当天检查次数、去重发现数、主源成功次数、最近一次主源成功时间、两源当前状态；次日日报补齐完整一天。这里“成功”指主源抓取成功，不代表SMTP送达。中断整天的日期无运行记录，不凭空补造统计。
8. 状态保存在独立的 `monitor-state` 分支，每次运行结束即使抓取或SMTP失败也尝试保存。未使用会过期的缓存作为唯一数据库。同一仓库并发串行，避免同时写状态；多个独立部署之间不共享去重记录。

## 状态与费用

`monitor-state` 中的 `monitor.sqlite3` 含公告、邮件正文（不含SMTP凭证或收件地址）、发送状态和运行统计。公开仓库意味着这些状态可见。更在意隐私可用私有仓库，但需关注Actions分钟数。

公开仓库标准GitHub托管运行器通常免费；私有仓库依套餐有免费额度，超出可能收费。全天10分钟约每日144次、每30天4320次；若每次按1分钟计费即约4320分钟，**不能保证私有仓库零费用**。5分钟频率约翻倍。默认不调用任何AI服务。[GitHub计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

不要删除或回退状态分支，否则已有公告可能重新提醒。状态每轮提交会积累Git历史，长期运行后应备份并维护状态分支体积；此项目没有自动清理审计记录。不要直接修改SQLite中的发送标记来“修好”失败，先检查失败原因。

## 本机运行与诊断

需要Python 3.12。Windows将`python`替换成可用的`py -3.12`也可以。

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# 将.env.example复制为.env并填配置；.env已被.gitignore排除
python -m monitor --probe
python -m monitor --heartbeat
python -m unittest discover -s tests -v
```

`--probe`只查公开源，不写状态、不发邮件。默认运行`python -m monitor`；`--heartbeat`请求当天心跳，但同一天不会重复发送。使用系统定时器每10分钟运行时要自行禁止并发。真实SMTP测试必须先填自己的Secrets并手动运行；本包没有内置账号，也没有替你发过任何邮件。

如果上交所接口变化，主要修改`monitor/sources.py`中的`SSE`；巨潮变化改`CNInfo`。本地可用`SSE_ENDPOINT`覆盖主源查询端点；返回结构变化仍需改解析器。添加新源实现同样接口，并在`monitor/__main__.py`注册，不能让新源异常变成空列表。

## 可选AI摘要（默认关闭）

原始公告步骤以及状态保存完成后，独立的AI步骤才会启动；AI不参与发现、去重或原始邮件投递决策。默认不安装PDF依赖、不读取AI密钥、不调用API。

开启方法：设置Variables `ENABLE_AI=true`、`AI_ENDPOINT`（完整HTTPS聊天补全接口地址）、`AI_MODEL`（你的服务商支持的模型名），并设置Secret `AI_API_KEY`。例如采用DeepSeek的`https://api.deepseek.com/chat/completions`协议，模型名请按你的账号及[服务商文档](https://api-docs.deepseek.com/api/create-chat-completion/)配置；这里不预置会过时的模型名。

本地需先`pip install -r requirements-ai.txt`，再运行`python -m monitor.ai`。只处理最近7天内已发送原始提醒的公告，每轮最多2条，每条最多3次分析尝试。下载最大10MB，提取前20页、最多20000字符；不做OCR。摘要明确标注截断及可能错误，附原文链接。API或PDF解析失败时追加一封“AI分析失败，原始提醒已发送”，不会撤销原始提醒。摘要发件失败也进入队列重试。开启AI后会把公开公告节选发送给指定模型服务商，并可能计费。

## 文件说明

- `.github/workflows/monitor.yml`：10分钟定时、手动启动、状态恢复/保存、可选AI。
- `.github/workflows/tests.yml`：离线回归测试。
- `monitor/sources.py`：双源抓取及标准公告结构。
- `monitor/store.py`：去重、统计、持久化待发送队列。
- `monitor/mail.py`：TLS SMTP与固定Message-ID。
- `monitor/__main__.py`：告警、心跳、投递流程。
- `monitor/ai.py`：可选独立PDF摘要。
- `tests/`：关键失败与重试路径测试。

## 交付验证

2026-09-07，本地只读实测：上交所与巨潮均返回603398的2026-09-05公告，两源标题相同、各自PDF地址有效格式。该结果仅证明当次请求成功，不代表GitHub运行器日后的可达性。

官方页面：[上交所公司公告](https://www.sse.com.cn/assortment/stock/list/info/announcement/)、[巨潮资讯](https://www.cninfo.com.cn/new/index)。完整验证范围与未测试项见`VALIDATION.md`。
