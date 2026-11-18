# 棠原试验室碳因子结算册

合成用电/燃料/出行活动→因子核算与差异说明→站内周期结算；范围声明与缺因子拒绝

## 业务场景与工作流
模拟客户：棠原研究协作组，12位业务与分析人员。每月研究明细先从`carbon-factors-202611.csv`录入底稿，经口径冻结、Python核算、负责人复核后向业务阅读者交付站内CSV。原有流程依靠人工表格抄算，痛点为口径版本难追溯、重复计算易错、复核与交付脱节。

模块：业务总览、电费账单对账、业务底稿、新建口径版本、异步核算与复核、定时交付、站内收件箱、审计。底稿可新增/修改/删除；口径仅新增版本；报告提交时冻结来源与版本并保存SHA256。定时交付引用已复核报告快照，worker到期写入数据库，业务阅读者下载。

## 电费账单对账
电费账单一行常合并多条用电底稿。页面可录入账单（月份、部门、电量、单位），逐行追查底稿；后端按「月份 + 部门」把该行与互不重叠的**电力**底稿组合求解，账单电量与底稿电量统一换算为 kWh（支持 kWh/MWh）后要求**精确相等**（Decimal 比较，无容差）。

- 整张账单所有行一起求解；只有全单**唯一解**才落库认领底稿。
- 无解（合计不足、没有精确组合、跨行争用同一底稿）或多解（≥2 套互不重叠组合）时返回争议状态，逐行给出可用底稿数、候选组合数、候选样本与具体原因，**不认领任何底稿**。
- 认领写入 `bill_claims`，底稿 `source_id` 唯一约束 + 数据库唯一索引保证并发不重复认领；对账在「账本版本 → 账单」锁序下将计算结果一次性生效，重试已生效账单直接返回既有认领，不新增。
- 底稿修改/删除会递增账本版本，并让引用该底稿的已对平账单整体置为「已失效」、释放认领；求解期间若有底稿变动，本次结果作废（409），不产生部分认领。
- 已对平账单可用于核算：提交报告时把该账单认领的底稿与账单元数据冻结进快照；此后底稿改动不影响已提交报告。排队期间认领底稿仍保留在账单上；旧报告始终按提交时快照取值。
- 候选组合枚举有步数预算；超出时按「无法确认唯一解」的争议处理而不认领。

实现：`backend/src/services/matching.py`（求解）、`bill.py`（对账/认领/追查）、`ledger.py`（账本版本与失效），模型 `backend/src/models/bill.py`，前端页面 `frontend/src/pages/bills.js`。

## 算法口径
单位转换、因子版本冻结、用量×因子与部门透视。kWh/MWh单位换算手算；缺单位因子拒绝；因子更新旧报告不变。

活动量 × 因子 · 单位与范围随报告保存。输入：业务底稿与口径JSON；输出：summary、透视rows、method、warnings。算法实现在`backend/src/services/calculation.py`，输入校验在`validation.py`。固定seed=42，240条合成数据＋3条账单对账样例；未对接真实业务系统。修改源数据和新建因子/口径不会改变已提交报告。

## 一天的验收剧本
1. 09:00 admin查看243条seed底稿与具名业务期间。
2. 09:10 analyst添加`carbon-factors-202611.csv`的一条具名业务记录。
3. 09:12 analyst录入「办公区十二月电费账单」一行 800 kWh，整单对账后逐行查看认领的 3 条电力底稿（500+100+0.2MWh）。
4. 09:20 admin在口径版本页登记新版本；旧版本保留。
5. 09:30 analyst命名“十一月核算”，选择期间/版本，或直接选已对平账单排队。
6. 09:31 Redis worker真实执行Python计算，展示汇总/透视与口径边界。
7. 09:40 admin核对数值后填写至少4字复核说明。
8. 09:45 admin建立60秒周期计划，worker到期生成站内持久化交付。
9. 09:46 viewer在站内收件箱下载交付CSV；审计页查看操作者与时间。

## 快速启动
```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
前端 http://localhost:18254 ，后端 http://localhost:19254/docs ，数据库宿主端口17754。中文父目录可用。恰好5服务frontend/backend/worker/db/redis；均有真实healthcheck。停止并清空演示数据：`docker compose down -v --remove-orphans`。

|角色|邮箱|密码|权限|
|---|---|---|---|
|admin|admin@demo.local|Demo2026!|全部底稿、口径版本、核算、复核、调度|
|analyst|analyst@demo.local|Demo2026!|底稿增改删、核算、导出；无复核/调度/口径写权限|
|viewer|viewer@demo.local|Demo2026!|只读；仅可下载已复核报告与站内交付|

## 工程结构与开发
Python3.11/FastAPI/SQLAlchemy/Redis，PostgreSQL16；前端原生HTML/CSS/JS ES modules，Nginx代理`/api/`剥前缀至后端。`frontend/src`按api/stores/pages/components/hooks/utils/router/constants/types拆分；`backend/src`按models/schemas/services/controllers/routes/middlewares/config/utils/constants拆分；`backend/scripts/seed_demo.py`自然键幂等且不覆盖用户修改。SQLAlchemy启动创建初始结构，迁移说明在backend/migrations。

后端开发：在backend安装requirements，设置DATABASE_URL/REDIS_URL/JWT_SECRET，`uvicorn src.main:app --reload`；前端可用Nginx或带同等代理的静态开发服务器。前后端全部业务计算在Python。

环境变量：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、JWT_SECRET（部署替换）、DATABASE_URL、REDIS_URL、SEED_DEMO。`.env`不入Git。数据库/Redis/共享产物为命名卷。共享角色枚举admin/analyst/viewer与状态queued/running/completed/failed分别定义于backend/src/constants/enums.py和frontend/src/constants/enums.js。两个中间件：统一可恢复校验错误、每IP每分钟360请求限流。

## 已知边界
定时分发为站内收件箱；一名worker轮询执行，无外部邮件。调度发送已复核固定报告快照，不自动重算新数据。时间显示Asia/Shanghai。报告导出适合小型数据；底稿全量加载243行演示数据。Google Fonts不可用时回退serif。后端API以专属字段对象收发，页面通过业务输入控件编辑，不要求用户写JSON。新增来源期间自动可选；专属字段和算法严格校验。JWT演示有效期2小时。

MIT License。

## 实际实体映射
来源记录（Source，专属业务字段校验）、电费账单（Bill/BillLine）、底稿认领（BillClaim）、口径版本（Policy）、核算报告（Run）、交付计划（Schedule）、交付（Delivery）均经数据库→模型→请求schema→service→route/controller→frontend API→store→page。Period为已有来源期间字典，在任务期间选择器使用；不单独作为复杂度计数。底稿JSON为小型多字段单记录存储，不虚构未落库的子实体。

实际表：期间字典 activity_periods；专属来源 activities（含乐观版本 revision）；冻结口径 emission_factors；异步报告 carbon_runs；电费账单 electricity_bills / bill_lines；底稿认领 bill_claims；账本版本 ledger_state。

## 测试
`cd backend && python -m pytest tests -q`（需先安装 requirements 与 pytest；测试使用 SQLite 与独立临时库，不依赖 Postgres/Redis。演示/CI 的 Postgres 行为以 docker compose 为准）。
