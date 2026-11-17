# 滨海曲面场加工归档

本地 Python 3.11 标准库工具，供滨海电磁实验室的数据员登记求解器曲面数据，供质控员执行精确采样、逐面环流、边界和面总量核账。SQLite 保存历史源修订、固定计划及完整成果。无需安装或网络。

## 一天的工作

1. 打开归档：缺失结构初始化，已有数据和当前引用保留；样例不会自动装载。
2. 接收求解器节点及三角面：检查真实三维非退化性，保留节点和面身份。
3. 核对普通边方向：节点 ID 字符串序决定规范方向，面保留求解器绕序。
4. 导入完整节点标量、边积分、面总量：三类身份必须精确覆盖，登记整批修订。
5. 质控员指定历史修订及有序重心采样队列，冻结分析计划。
6. 执行精确插值和逐面有向环流，生成边界、内部残余和有符号差额。
7. 成果与完成回执共同发布；失败可重试，成功重放返回原成果。
8. 上游更正后按旧成果 ID 读取、重开及导出，离库核账继续使用原输入。

## 数据口径

输入 mesh={'nodes': {ID: (Fraction, Fraction, Fraction)}, 'faces': {ID: (v0,v1,v2)}}，一批 1–60 个面。ID 是非空字符串。每面三个不同节点且三维叉积非零；每条普通边至多有两个关联面。同坐标不同身份不会焊接，重复几何合法。坐标无需位于 XY 平面。

fields 含 node_scalars、edge_integrals、face_totals 三个字典；边键必须为 (较小节点ID,较大节点ID)。所有坐标、字段和权重必须为 Fraction，不接受 int、bool、float 或 None 替代。零、负数和大分子分母合法。节点、边、面字段各恰好覆盖完整身份集合。输入及返回容器均独立。

求解器采用统一长度单位；节点场单位一致，边积分和面总量采用同一核账单位。程序不自动换算单位。采样位置和值按原面角顺序作重心线性组合；不排序权重，不跨面搜索。样本为 {'face': ID, 'weights': (w0,w1,w2)}，三个非负 Fraction 严格和为 1，保留顺序和重复项；空队列仍计算全部账。

面环流为三条规范边积分乘各次面边符号后求和。边界边只关联一个面；内部边关联两个面。反向共边抵消，同向共边留下真实内部残余，程序不自动翻面。面总量按原值保存，差额为环流减面总量；非零差额是质控结论，可正常发布。V1 使用普通端点对边，不进行坐标吸附、曲面修复或自动定向。

## 公开 Python API

从仓库根目录运行，无需包安装：

```python
from fractions import Fraction as F
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import load_solver_sample, check_export

mesh, fields, samples = load_solver_sample("samples/normal.json")
store = ArchiveStore.open("quality.sqlite")
try:
    sources = SourceCatalog(store)
    receipt = sources.put_source("solver-1", mesh, fields)
    revision_id = receipt["revision_id"]
    plan_id = PlanService(store).create_plan("qc-1", revision_id, samples)
    result_id = AnalysisService(store).execute(plan_id)
    result = ResultReader(store).read_result(result_id)
    data = ResultExporter(ResultReader(store)).export_result(result_id)
    print(result["payload"]["circulations"])
    print(check_export(data))
finally:
    store.close()
```

put_source 返回 {'revision_id': int, 'fingerprint': str}；read_source(revision_id) 返回 revision_id、fingerprint、mesh、fields 的独立快照；current_revision() 返回当前 ID 或 None。每个新源请求产生完整新修订。相同源请求及完整规范内容返回原回执，即使已有后续更正也不倒退当前引用。源比较包含坐标、面绕序和全部字段，不只比较摘要。

create_plan 返回整数 ID；PlanService.read_plan 返回 AnalysisPlan。计划冻结确切修订、摘要、完整队列和 ordinary-surface-v1 口径。源请求与计划请求使用两个独立空间。同号异绑定抛 surface.sources.revisions.ConflictError；非法领域值抛 ValueError，缺少修订、计划或成果抛 KeyError。数据库异常原样传播。

execute 返回整数成果 ID。read_result 返回 {'result_id', 'fingerprint', 'payload'}；payload 含 binding、完整 input、requests、samples、circulations、face_totals、balance。balance 明确保存逐面差额、边界及内部各边项和总账。完成计划重放返回原成果，不重算；损坏成果明确报错。读取和导出只访问成果表，不访问当前源或执行器。

ArchiveStore 管理单连接及事务；SourceCatalog、PlanService、AnalysisService 和 ResultReader 共同使用该连接。各写入由所属服务提交，禁止嵌套事务。不提供多线程调度或进程恢复。store.fault 默认为 None；测试可安装接收阶段字符串的函数并抛异常。阶段包括 source_after_revision/source_after_request/source_after_head、plan_after_insert、compute_after_samples、publication_after_result/publication_after_receipt、export_before_encode。任何异常传播给调用者，失败不得伪装成功。

## 精确成果与离库核账

导出是确定性 ASCII JSON 字节，外壳为 format=surface-exact、version=1、payload。$fraction 保存既约整数分子及正分母，$tuple 保留元组，$map 保存稳定排序的键值项，列表保持顺序。拒绝重复 JSON 成员、重复映射身份、非既约分数、非法分母及不支持版本。成果携带完整源及计划摘要。

ResultRepository 验证载荷结构、完整身份和固定绑定；读取不会补算或修复数值。check_export(bytes) 是显式独立复核：仅遍历导出原始面绕序、坐标和固定字段，重新核对样本、环流及全部账；不调用任务执行器或场计算函数，不连接源库。可用 python3.11 -B -m surface.results.checking --check 导出文件 来核验自行保存的导出。

## 手算样例与运行

samples/normal.json 为统一单位下两面 f1=(A,B,C)、f2=(B,D,C)。AB=1/3，AC=1/7，BC=2/5，BD=3/7，CD=-1/2，节点标量为 0、2、4、6，面总量为 0、-2/3。f1 环流为 1/3+2/5-1/7=62/105；f2 为 3/7+1/2-2/5=37/70。共边抵消，边界为 47/42，总差为 25/14。f1 权重 (1/2,1/3,1/6) 的标量为 4/3。samples/invalid.json 保留相同字段但将 A、B、C 放到同一直线，必须拒绝。

```sh
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B -m surface.results.checking --demo samples/normal.json
```

运行清单为 .pointwise-runtime.json，setup 为空。demo 在测试临时目录建立归档，执行、发布、更正、关闭重开、导出及离库核账；断言后输出实际 POINTWISE_SCENARIO JSON。临时目录退出即清理。完整测试失败返回非零。测试覆盖竖直面、重复几何身份、严格字段覆盖、请求冲突、非法权重、固定修订、重复采样、同向内部残余、源和成果事务回滚、重试、历史读取、精确大分数、损坏载荷及导出阶段失败。SQLite 授权器实际拒绝源和计划读取时，成果读取导出仍须成功；实际触发器拒绝回执写入时，成果提交必须回滚。

## 代码职责

sources 负责输入、修订、请求绑定和当前引用；mesh 负责三维几何、普通关联、边界链和重心定位；fields 负责完整字段绑定、插值、环流及核账；tasks 负责固定计划、确切输入解析、内存计算和统一发布；results 负责精确编码、固定载荷读取、导出和独立复核。24 个生产模块均接入上述公开链路。测试夹具围绕同一求解器批次，不在初始化时修改用户数据。
