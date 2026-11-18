# 初始结构

SQLAlchemy启动创建以下业务表：activity_periods, activities, emission_factors, carbon_runs, delivery_schedules, deliveries、users、audits。Source专属JSON字段由对应validation严格校验；Policy为不可覆盖的版本记录；Run保存source/policy冻结快照；Period仅用于已有期间选择器，不计入核心复杂度。已有数据库升级需先备份，后续字段演进应以独立迁移执行。

# 002 电费账单对账

新增表：electricity_bills、bill_lines、bill_claims、ledger_state。`activities` 增加 `revision`（默认 0，用于检测底稿变更）。

- `electricity_bills.result` 保存最近一次对账的逐行诊断（候选数、候选样本、原因、状态）。
- `bill_claims.source_id` 有唯一约束，保证同一底稿不被两张账单重复认领；`line_id` 指向 `bill_lines`。
- `ledger_state` 单行记录账本版本；底稿新增/修改/删除递增该版本，对账在求解前后比对版本以作废旧解。
- 旧库升级：`ALTER TABLE activities ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;` 后再由 SQLAlchemy 创建新表，历史底稿 revision 从 0 起算即可。
