# 重新三角化交接说明

RETRIANGULATION.md 是需求口径，本文件是交付侧的简短表示与交接不变量，供不读实现的数据员核账。所有数值全程 Fraction，无浮点、无开方。

## 简短表示

新增三个生产模块，沿原公开链路接入，不改旧接口行为：

- `surface/mesh/planar.py`：公共平面的有理 2D 坐标系（原点+法向+两轴，投影即点积），以及"无洞简单平面区域合法三角划分"判定——共面、绕序同向、同网格坐标唯一、节点全被使用、两两三角形交集仅为空/公共顶点/公共整边、面经公共边连通、边界为单一度数二的闭环。
- `surface/mesh/overlay.py`：网格侧证据。目标节点定位（排序首个合法承载源面）、目标规范边按源边交点与共线重叠分段（0=t首<…<t末=1，正长不重不漏，共线共享源边只记一段）、源目标面正面积交叠（在源面重心 (λ1,λ2) 空间对参考三角形做精确 Sutherland–Hodgman 裁剪）。同区域判定为双向面积覆盖精确相等，不只比总面积。
- `surface/fields/transfer.py`：字段侧消费同一证据。节点=分片线性插值；边=逐段 Whitney 一形式积分 `Σ(i<j) eij(λ̄iΔλj−λ̄jΔλi)`（λ̄ 为段端点均值，Δλ 为端点差，与参数化无关）；面总量=Σ 交叠面积/源面面积 × 源总量，零负合法，不重归一化。

计划、执行、成果、导出复用原表与原事务/故障端口：`PlanService.create_retriangulation_plan` 与旧 `create_plan` 共享请求空间；`AnalysisService.execute` 按计划种类分派；`ResultReader`/`ResultExporter` 对两种成果同样工作；`check_export` 按 kind 分派，旧成果核验路径逐字节保留。

## 交接不变量

1. 绑定不变量：旧计划 binding() 仍是 4 键、摘要不变；新计划 binding() 恰为 revision_id、source_digest、samples、convention=retriangulated-whitney-v1、target_mesh（规范 OrdinaryMesh 记录）。成果 binding 五键不变，plan_digest 绑定新计划完整 binding()，source_digest 仍绑定原始源而非目标。
2. 证据不变量：transfer 恰含 nodes/edges/overlaps 三键；节点与边证据完整覆盖目标节点与规范边；边分段区间首尾相接、每段正长、端点权重在承载源面闭三角形内重建对应参数点；交叠 (源面,目标面) 唯一、多边形≥3顶点不重复首点、按公共方向绕行（(λ1,λ2) 鞋带面积为正）、source_fraction 恰等于 2 倍该面积，且每个源面的分数和精确为 1。
3. 字段不变量：目标三类字段只能由保存的证据加固定源字段精确重导——离库核验器（check_export 扩展路径）不调用任何执行器或几何代码，独立重算节点值、逐段 Whitney 积分、面积比与面总量并全部比对；目标环流、采样、总账由目标网格与目标字段按 V1 同一算法得出，不以环流回填总量。
4. 总量守恒：覆盖完整时 Σ目标面总量=Σ源面总量；目标边界总账等于沿目标边界的转移边积分代数和。非零差额仍是正常质控结论。
5. 冻结不变量：计划冻结确切修订、目标网格与有序采样；提交后修改传入容器、源更正、修改 read_plan/read_result 返回容器、关闭重开均不漂移；完成计划重放返回原成果不重算；读取与导出不重新求交或转移（compute_after_samples 故障注入下读取导出仍成功）。
6. 失败不变量：几何或覆盖非法在计划创建时抛 ValueError 且不留计划或请求绑定；缺修订抛 KeyError；同号异绑定抛 ConflictError（新旧计划互斥同号）；计算、验证或发布任一失败不留新成果与完成回执，计划保留可重试；回执表触发器拒绝时成果提交整体回滚。

## 独立小例核账

手算夹具在 retriangulation_cases.py（换对角线、非均匀扇形交叠、恒等+共线共享边、反向规范边与竖直平面重命名、非凸边界恒等），补充小例在 tests/test_retriangulation.py：

- 源顶点穿越：带中心点的四面源对三角划分目标，目标对角线穿过源顶点 M，分段为 [0,1/2]、[1/2,1]，AC 转移恰为 1。
- 边界共线分点：目标在 AB 中点合法加点，AM=1/2、("B","M")=−1/2、DM=1/3，面总量 7/3、5/3、4（合计 8 守恒），check_export 边界总账 1。
- 拒绝：T 接点（区域相同也拒）、洞（双边界环）、点接触粘连、同网格重合身份、未用节点；夹具另覆盖错位、缺口、重叠、非共面、绕序冲突。
- 新旧共存：同库同修订各建一种计划，两种成果均可执行、读取、导出并过各自离库核验。

## 运行

```sh
python3.11 -B retriangulation_cases.py --verify   # 全部原回归 + 新旧公开联动样例
python3.11 -B retriangulation_cases.py --demo     # 非均匀交叠、源更正、真实磁盘重开导出
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B -m surface.results.checking --demo samples/normal.json   # 旧链演示不变
```
