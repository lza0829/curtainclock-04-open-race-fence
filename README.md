# Curtainclock（幕窗钟）

骨架：**批窗定义 → 算力槽/执行器资源依赖就绪 → 开窗租约占用 → 关窗释放与冲突水位互证**

- 前端：http://127.0.0.1:6100 （React + Vite）
- API：http://127.0.0.1:11100

## 争用口径（全局唯一）

**串行第一胜。** 两路几乎同时抢同一算力槽或同一执行器的同一时段时：

1. 开窗在 `BEGIN IMMEDIATE` 写锁内完成「双资源检查 → 占位插入 → 租约插入 → 批窗 live → 在途 +1」；
2. 先拿到写锁并提交的一路落 `open`，批窗置 `live`，在途 +1；
3. 后到的一路整笔失败：不留 `open`、不改批窗、不动在途，并在同一事务里写争用记录；
4. 两路都在写锁内分配回执序号，冲突桶里可对出「争用回执 / 对手回执」。

没有任何「无互斥的先读后写」路径。即使绕过应用层插入，`resource_locks` 上的
`(kind, resource_code, day, start_min, end_min)` 唯一约束也会拒绝第二笔同资源同窗口占用。

> 另一臂「双方均败」未采用，设置接口固定为 `reject`，不可切换。

## 同源核对

| 展示位 | 事实源 |
| --- | --- |
| 总览「在途租约」 | `leases WHERE status='open'` 计数 |
| 总览「冲突计数」 | `conflicts` 行数 |
| 总览「占用索引」 | `resource_locks` 行数（应为在途 × 2） |
| 槽位页 / 执行器页「当前占用」 | `GET /api/occupancy` |
| 冲突页「争用 / 对手回执」 | `conflicts.attempt_seq / peer_seq` |

`GET /api/dashboard` 额外返回 `consistent`：当且仅当水位缓存、租约事实表、
占用索引三者互相吻合时为 `true`。

## 本地冒烟（推荐）

```bash
python3 -m venv .venv
./.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=$PWD/backend ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 11100
```

前端需 Node ≥18：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:6100 。

## 并发夹具

服务需已起在 11100，且使用干净库（`CURTAINCLOCK_DB` 可指向临时库）：

```bash
python3 -I _smoke_api.py     # 单路开窗 → 冲突 → 关窗，在途归零
python3 -I _race_open.py     # 同毫秒双开：同槽 / 同执行器 / 同批窗三组
python3 -I _race_stress.py 10  # 每轮四路抢同一窗口，重复 10 轮
```

`_race_open.py` 核对：同资源至多一笔 `open`；恰好一路 200、一路 409；
失败方 `peer_seq` 等于胜方 `receipt_seq`；刷新后在途与租约一致；争用页可查双方回执。

## Docker

```bash
docker compose up -d --build
```
