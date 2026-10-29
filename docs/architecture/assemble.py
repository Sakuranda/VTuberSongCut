"""Normalize reviewed evidence into the architecture atlas content (no runtime imports)."""
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
fe=json.loads((HERE/'evidence/frontend.json').read_text())
be=json.loads((HERE/'evidence/backend.json').read_text())
de=json.loads((HERE/'evidence/delivery.json').read_text())
nodes=[]
for group,data in [('fe',fe),('be',be),('delivery',de)]:
 for n in data['nodes']:
  layer=('ui' if n['kind']=='ui' else 'ts') if group=='fe' else ('delivery' if group=='delivery' else {'domain':'core','application':'core','infrastructure':'runtime','policy':'api'}[n['layer']])
  if n['id'] in ['be-api','be-recorder-api']:layer='api'
  status=n['status'] if n['status'] in ['implemented','external','interface','planned','partial'] else 'interface'
  nodes.append(dict(id=n['id'],title=n['title'],layer=layer,status=status,summary=n.get('summary',n.get('responsibility','')),responsibilities=n.get('features',[]),sources=n.get('files',[]),risks=n.get('notes',[]),tech='React / TSX' if group=='fe' and n['kind']=='ui' else 'TypeScript' if group=='fe' else 'Python' if group=='be' else 'Rust / Tauri' if n['id']=='delivery-desktop' else 'Build / Config'))
for ext in be['externals']:
 nodes.append(dict(id=ext['id'],title=ext['name'],layer='external',status='external',summary=ext['boundary'],responsibilities=[ext['boundary']],sources=[dict(path=p,line=1,symbol='依赖接入位置') for p in ext['refs']],risks=['此节点是外部依赖，不表示其代码由拾歌实现。'],tech='External'))
nodes.append(dict(id='planned-ts-services',title='建议：独立 TS 应用服务',layer='ts',status='planned',summary='把状态与流程从组件迁出，以服务类复用；这是重构目标，不是已有模块。',responsibilities=['EditorSession 管草稿与保存流程','PreviewCoordinator 管清晰度与异步响应','TrainingController 管训练工作区用例'],sources=[dict(path='apps/web/src/main.tsx',line=122,symbol='App（当前迁出位置）')],risks=['上述类目前尚未创建。','纯时间计算与校验保留函数，服务端继续执行最终校验。'],tech='Proposed TS classes'))
byid={n['id']:n for n in nodes}
byfile={}
for n in nodes:
 for s in n['sources']:byfile.setdefault(s['path'],n['id'])
edges=[]
def edge(a,b,label='调用 / 依赖',kind='control'):
 if a in byid and b in byid and a!=b and not any(e['from']==a and e['to']==b for e in edges):edges.append({"from":a,"to":b,"label":label,"kind":kind})
# Avoid the reserved Python keyword while keeping the serialized graph conventional.
for e in be['edges']:
 edge(e['from'],e['to'],e.get('label','调用 / 依赖'),'planned' if e.get('kind') in ['planned','contract'] else 'data' if e.get('kind')=='storage' else 'control')
for n in fe['nodes']:
 for dep in n.get('dependencies',[]):edge(n['id'],dep)
for n in de['nodes']:
 for dep in n.get('dependencies',[]):edge(n['id'],dep if dep in byid else byfile.get(dep,''),'运行 / 构建引用')
for a,b,label in [('fe-api','be-api','HTTP / JSON / Range'),('fe-recordings','be-recorder-api','录播查询与控制'),('fe-training','be-training','经 HTTP 调用'),('delivery-desktop','fe-app','WebView 复用网页'),('delivery-server','be-api','启动同一服务'),('be-media','external-ffmpeg','子进程媒体处理'),('be-store','external-sqlite','事务 / 持久化'),('be-recorder','external-recorder','REST + Webhook'),('be-inference','external-autoslicer','模型 / 特征 / 后处理'),('be-training-worker','external-autoslicer','调用上游训练')]:edge(a,b,label)
edge('fe-app','planned-ts-services','建议迁出交互用例','planned');edge('planned-ts-services','fe-api','建议经端口调用','planned')


def ref(node,symbol=None):
 sources=byid[node]['sources']
 return next((s for s in sources if symbol and symbol in s.get('symbol','')),sources[0] if sources else None)
def step(node,title,detail,symbol=None):return dict(node=node,title=title,detail=detail,source=ref(node,symbol))
def feature(id,title,description,steps,outputs,caveats,tests,status='implemented'):
 return dict(id=id,title=title,status=status,description=description,steps=steps,outputs=outputs,caveats=caveats,tests=tests)
features=[
 feature('recording','B站录制与自动入库','录制器独立保存原片；文件完成后经认证通知登记，预览进入拾歌计算队列。',[
  step('fe-recordings','添加 / 控制房间','录播库调用管理员接口；普通账户只浏览已归档素材。'),
  step('be-recorder-api','接收控制与通知','控制走账户权限；精确的 webhook POST 使用独立随机密钥。'),
  step('be-recorder','持久去重 / 稳定检查','校验完成事件、文件大小、路径与稳定性；相同事件不重复入库。'),
  step('be-api','登记共享素材','import_recording 按确定ID创建或复用Project，原片只读引用。'),
  step('be-studio','提交预览','按源签名和规格复用或提交low预览。'),step('be-jobs','串行执行','有界公平队列承接后台工作，不在回调请求中转码。'),
  step('be-media','生成320p','FFmpeg生成共享低清文件，进度及临时输出受控。'),step('fe-recordings','打开剪辑','用户打开录播；编辑者先创建个人副本。')],
  ['SQLite：recordings / recorder_events / recorder_state / projects / jobs','原片仍在录播目录，预览进入 data/previews/'],['录制与B站投稿是不同能力。','本轮支持已关闭文件；不读取仍在录制中的文件。','录播姬是外部 GPL-3.0 服务。'],['services/studio/tests/test_recorder.py','services/studio/tests/test_recorder_flow.py']),
 feature('preview','导入与双清晰度预览','上传落盘后生成共享320p；高清按需准备，UI只切换播放来源和保留时间位置。',[
  step('fe-api','上传文件','XHR报告实际传输进度，字节上传完毕后仍等待素材检查。'),step('be-resource-policy','检查预算','请求流和落盘阶段检查大小、并发及磁盘余量。'),step('be-api','探测 / 建项目','读取媒体规格，登记原片与所有者。'),step('be-studio','选择衍生缓存','源签名+规格版本确定共享预览；不会修改原始录播。'),step('be-jobs','排队处理','相同准备请求复用任务，全局执行位受限。'),step('be-media','生成 / 复用播放版','low强制最高320p；high最高1080p，兼容时复制码流。'),step('fe-playback','合并准备结果','只合并预览元数据，不能覆盖未保存草稿。')],['Project媒体元数据；共享低清/高清文件','320p是播放副本，导出和检测仍读取原片'],['旧兼容预览不能直接冒充320p。','预览完成不增加编辑revision。'],['apps/web/tests/uploads.test.mjs','apps/web/tests/playback.test.mjs','services/studio/tests/test_preview_profiles.py']),
 feature('editing','边界编辑与保存','鼠标、键盘和时间码统一更新源时间轴上的草稿，再携带版本保存。',[
  step('fe-timeline','拖动 / 逐帧','交互改变入点出点；不会发起视频转码。'),step('fe-timecode','输入时间码','输入验证、格式化和键盘微调。'),step('fe-editing','纯规则校验','毫秒/帧换算与项目校验，适合继续复用纯函数。'),step('fe-project-flow','草稿 / 保存','App比较编辑快照、提交revision并处理冲突。'),step('be-domain','最终领域校验','服务端校验区间、歌词和导出参数。'),step('be-store','乐观锁写入','UPDATE要求revision匹配，防止静默覆盖其他页面的修改。')],['Project / Clip元数据更新','原片、预览文件不因调整边界而变化'],['当前草稿和保存编排仍内嵌App。','服务端校验不能随UI重构而移除。'],['services/studio/tests/test_core.py','apps/web/tests/project-edits.test.mjs']),
 feature('export','快速 / 精确导出','提交时固定项目快照；重任务排队，下载访问仍要经过项目权限检查。',[
  step('fe-export-controls','选择规格','精确/快速、视频/音频、码率/分辨率等是Clip设置。'),step('be-resource-policy','限制耗时与规格','低配模式限制导出长度、分辨率、编码和帧率。'),step('be-studio','固定快照 / 复用结果','相同渲染设置可复用已有媒体；每人仍保留独立快照。'),step('be-jobs','公平排队','每人和全局配额；结构化任务可以重启后重新处理。'),step('be-media','执行FFmpeg','快速复制码流；精确模式重编码并可烧录字幕。'),step('be-api','受控下载','只提供任务声明的产物，核对项目访问权限。')],['MP4、MP3、可选ASS字幕和项目快照','下载文件属于任务上下文'],['快速切点受关键帧影响，不等同逐帧精确。','导出始终使用高清原片。'],['services/studio/tests/test_export_modes.py','services/studio/tests/test_shared_derivatives.py']),
 feature('detection','CPU演唱区间检测','已训练模型判断目标主播是否在唱歌；当前不自动识别歌名。',[
  step('fe-models','选模型 / 设备','项目必须显式选择目标主播模型。'),step('be-models','校验模型与设备','解析模型路径、哈希和可用设备。'),step('be-studio','提交检测','源身份与模型/参数进入任务和缓存键。'),step('be-detector','启动模型进程','CPU/auto走stream入口，显式GPU沿原路径。'),step('be-inference','分块音频推理','流式读取音频窗口，复用上游模型/特征/后处理。'),step('be-studio','合并区间','保留已有手工片段；模型已切换则拒绝写入旧结果。')],['低帧率概率与演唱区间','Project新增待识别歌曲片段'],['CRNN默认120秒窗口；TCN保留较长上下文。','弱Linux真实速度和识别精度必须实测。'],['services/studio/tests/test_inference_streaming.py','services/studio/tests/test_models.py']),
 feature('training','本地标注与模型训练','独立训练工作区准备数据、保存标注，交给训练服务和独立worker。',[
  step('fe-training','创建数据集 / 上传','主播名称、训练/验证用途与复核条件。'),step('fe-annotation','标注演唱区间','视频快捷键、时间范围、演唱者与复核状态。'),step('be-training','校验 / 固定快照','检查数据集版本和准备度，使用独立训练队列。'),step('be-training-worker','提取特征 / 训练','校验素材哈希，生成LogMel和标签清单，调用上游train。'),step('be-models','登记成功模型','有限值、格式和语义校验后进入模型库。')],['training_datasets / training_jobs','特征、日志、检查点和受管模型'],['训练不走媒体JobRunner，而是TrainingService自身工作器。','低配服务器关闭训练，本地正常模式保留。'],['services/studio/tests/test_training.py','apps/web/tests/training-recovery.test.mjs']),
 feature('models','模型导入与模型库','检查点验证在独立模型进程执行；模型存为受管资产。',[
  step('fe-models','选择模型文件','导入已有.pt/.pth，填写可选名称。'),step('be-models','复制 / 哈希 / 登记','按SHA-256管理文件，维护模型来源。'),step('be-model-probe','隔离验证','weights-only CPU加载，验证结构、形状和有限值。'),step('fe-models','带回切歌','选择模型，保存到具体项目。')],['models表与受管权重文件'],['RVC模型不能直接充当演唱检测器。','模型库名称不代表权重自动识别了主播身份。'],['services/studio/tests/test_models.py']),
 feature('lyrics','LRC歌词与字幕样式','已有歌词的时间解析、预览与导出已实现；自动搜索和逐词对齐另属预留能力。',[
  step('fe-lyrics','导入LRC / 改样式','当前界面与歌词事件仍内嵌App。'),step('be-lyrics','解析与时间偏移','转换为源时间轴毫秒，检查范围与先后。'),step('be-domain','保存歌词结构','LyricCue与可选LyricWord遵守同一时间基准。'),step('be-lyrics','生成ASS','单行、双行、KTV布局；无真实逐词时间不假造精确逐字。'),step('be-media','烧录导出','需要字幕时由FFmpeg/libass执行。')],['Project中的歌词/样式','导出ASS或烧录后的视频'],['歌名、原唱和歌词仍需要人工/本地LRC输入。'],['services/studio/tests/test_core.py']),
 feature('accounts','多人权限与个人副本','共享素材与私人编辑元数据分开，复制项目不复制完整录播。',[
  step('be-access','识别账户与角色','管理员、编辑者和浏览者由服务端配置。'),step('be-api','核对项目访问','所有项目、下载和修改检查角色/所有权；令牌绑定账户。'),step('be-studio','复用媒体资产','副本仍引用同一源文件和衍生预览。'),step('be-store','保存独立草稿','各自Project ID/owner/revision。'),step('fe-project-flow','只读或个人编辑','无写权限时先创建自己的剪辑副本。')],['共享原片 + 每人独立Project','账户配置不进入前端或Git'],['目前不是匿名开放注册或计费系统。','后端权限不能只靠按钮禁用。'],['services/studio/tests/test_server_resources.py']),
 feature('cache','缓存与资源保护','按可再生数据、任务状态和引用关系清理；同时限制请求和重计算。',[
  step('fe-cache','查看 / 清理缓存','管理员查看分类大小及清理反馈。'),step('be-cache','重新核对候选','保护当前引用、原片、模型、导出与活动任务。'),step('be-resource-policy','执行预算','队列、上传、磁盘、连接和带宽受限。'),step('be-execution','取消与期限','终止拥有的子进程树；工作目录锁阻止重复服务。')],['缓存容量报告与清理计数','共享预览/检测缓存、构建缓存分别管理'],['自动回收仅处理符合条件且过期的可再生缓存。','不按文件名全盘删除PyTorch或用户素材。'],['services/studio/tests/test_cache.py','services/studio/tests/test_request_limits.py','services/studio/tests/test_execution.py']),
 feature('appearance','皮肤与外观','设置由独立TS控制器持有，通过React薄桥接更新显示。',[
  step('fe-appearance','选择外观','猫娘/简洁、配色、明暗模式。'),step('fe-theme','管理偏好与订阅','校验存储值，监听系统明暗与标签页变化。'),step('fe-welcome','展示角色与入口','装饰资产不覆盖视频或进入导出。')],['当前站点localStorage偏好','DOM语义色变量'],['当前控制器为工厂函数，未重构为AppearanceStore类。'],['apps/web/tests/theme.test.mjs'])
]
specific_symbols = {
 'recording':['RecordingsWorkspace','recording_router','RecorderService.accept','_create_app.import_recording','Studio.preview','JobRunner._worker','FFmpegMedia.preview','RecordingsWorkspace'],
 'preview':['uploadForm','RequestLimits.__call__','_create_app.upload','Studio.preview','JobRunner._worker','FFmpegMedia.preview','mergePreviewMetadata'],
 'editing':['Timeline','TimecodeInput','validateProject','save','Project.validate_clips','Store.save'],
 'export':['ExportControls','ResourcePolicy.validate_export','Studio.export','JobRunner._worker','FFmpegMedia.render','_create_app.artifact'],
 'detection':['ModelSelector','ModelLibrary.resolve','Studio.detect','AutoSlicerDetector.detect','main','Studio._detect'],
 'training':['TrainingWorkspace','TrainingAnnotation','TrainingService.start','main','ModelLibrary.register_file'],
 'models':['ModelManager','ModelLibrary.register_file','main','ModelSelector'],
 'lyrics':['importLyrics','parse_lrc','LyricCue','render_ass','FFmpegMedia.render'],
 'accounts':['Access.identify','_create_app.visible_project','Studio.with_previews','Store.create','cloneProject'],
 'cache':['CacheSettings','RuntimeCache.clean','RequestLimits.__call__','run_process'],
 'appearance':['AppearanceSettings','createAppearanceController','SkinWelcome'],
}
for flow in features:
 for item,symbol in zip(flow['steps'],specific_symbols.get(flow['id'],[])):
  refs=byid[item['node']]['sources']
  base=next((r for r in refs if r.get('symbol')==symbol),None) or next((r for r in refs if r.get('symbol','').split('.')[0]==symbol.split('.')[0]),refs[0])
  item['source']={**base,'symbol':symbol}
for i,future in enumerate(be['future']):
 status='interface' if future['status']=='contract-only' else 'planned'
 node_id=f'future-capability-{i}'
 node=dict(id=node_id,title=future['title'],layer='core',status=status,summary=('仅定义输入输出协议，尚无提供方实现。' if status=='interface' else '规划中的能力，当前没有远程任务协议或执行节点。'),responsibilities=[future['missing']],sources=future['evidence'],risks=['这里的代码是协议或当前边界，不表示目标能力已实现。'],tech='Python Protocol' if status=='interface' else 'Proposed capability')
 nodes.append(node);byid[node_id]=node
 edge(future['node'],node_id,'协议预留' if status=='interface' else '未来扩展，未接入','planned')
 features.append(feature('future-'+str(len(features)),future['title'],node['summary'],[dict(node=node_id,title='能力协议' if status=='interface' else '当前约束',detail=future['missing'],source=future['evidence'][0])],['尚无业务产物'],[future['missing']],[],status=status))
# Feature sources use actual node anchors; no invented future filenames are source links.
rec={x['id']:x for x in fe['recommendations']};bre={x['id']:x for x in be['recommendations']};risk={x['id']:x for x in de['risks']}
def sources(values):
 out=[]
 for value in values:
  if isinstance(value,str):
   path,_,line=value.rpartition(':');out.append(dict(path=path,line=int(line or 1),symbol='当前实现'))
  else:out.append(value)
 return out
plans=[]
p=risk['contract-drift'];plans.append(dict(id=p['id'],priority='P0',title=p['title'],problem=p['current']+' '+p['evidence'],target=p['proposal'],classes=['ProjectDTO','JobDTO','CapabilitiesDTO','GeneratedApiClient'],preserve=['Python是持久化与最终校验的事实来源。','先补响应模型，再自动生成TS类型；不是盲目套用旧OpenAPI。'],sources=p['files'],acceptance=['当前45个路由操作均进入生成快照。','权限/预览/任务字段有明确响应类型。','CI检查生成文件漂移。']))
for ident in ['ts-draft','ts-http','ts-preview','ts-training']:
 p=rec[ident];plans.append(dict(id=ident,priority='P1',title=p['title'],problem=p['reason'],target=p['plan'],classes=p.get('classes',[]),preserve=['组件仅接收快照并转发用户操作；服务类不导入React/Vue。','时间码、帧换算、校验继续调用现有纯函数。'],sources=sources(p['from']),acceptance=['不渲染UI即可验证流程和失败恢复。','原有草稿、取消、权限与重试行为不回退。']))
for ident in ['split-http-composition','repositories','typed-tasks']:
 p=bre[ident];plans.append(dict(id=ident,priority=p['priority'],title=p['title'],problem=p['current'],target=p['proposal'],classes={'split-http-composition':['ProjectRouter','ModelRouter','TrainingRouter','CompositionRoot'],'repositories':['ProjectRepository','TrainingRepository','RecordingRepository'],'typed-tasks':['PreviewTask','DetectionTask','ExportTask']}[ident],preserve=['保留已有Python资源类与明确生命周期。','不因拆文件改变原片、队列和权限边界。'],sources=p['evidence'],acceptance=['模块不再跨层读取对方私有状态。','现有行为测试通过，再删除重复路径。']))
p=risk['version-source'];plans.append(dict(id='runtime-config',priority='P2',title='统一版本与开发端口配置',problem=p['current']+' Vite开发代理还固定指向8765。',target=p['proposal']+' 代理目标从同一个服务地址配置读取，保留明确的本地默认值。',classes=['RuntimeConfig','ReleaseManifest'],preserve=['Web、桌面和服务器仍复用一套业务服务。','本地与服务器预算差异通过配置表达。'],sources=p['files']+risk['dev-port']['files'],acceptance=['版本/端口只改一处即可生成各端配置。','源码、打包和容器路径分别验证。']))
findings=[dict(title='当前不是“UI-only”结构',body='main.tsx文件有1930行（其中App函数1804行），TrainingWorkspace.tsx文件有901行；状态、请求、重试和业务判断混在视图文件。当前前端只有ApiError一个显式类。抽类应围绕有状态用例，不能仅把函数包进static类。',sources=[dict(path='apps/web/src/main.tsx',line=122,symbol='App'),dict(path='apps/web/src/TrainingWorkspace.tsx',line=1,symbol='TrainingWorkspace')]),dict(title='两套任务机制需要明确区分',body='媒体任务由JobRunner串行执行，训练由TrainingService自己的工作器执行。低配模式关闭训练；不能画成所有任务都自动经过同一个队列。',sources=[ref('be-jobs'),ref('be-training')]),dict(title='接口不等于已经实现的插件',body='歌名识别、歌词检索/对齐、封面、标题和发布都仍是Protocol边界。B站录制接入已经实现，但B站自动投稿没有实现。',sources=[ref('be-ports'),ref('be-recorder')]),dict(title='旧架构文档已经滞后',body='原ARCHITECTURE.md仍将桌面壳列为规划、队列恢复列为未来工作；本次图谱按当前分支源码重新核对，并将旧说明同步修正。',sources=[dict(path='docs/ARCHITECTURE.md',line=1,symbol='旧架构说明')])]
runtimes=[
 dict(id='local',title='本地源码',summary='浏览器访问本机API；同一Python服务协调模型、训练和FFmpeg。默认保留训练入口。',steps=['构建React/Vite静态资源，再通过scripts/dev.py启动API。','浏览器只调用同源HTTP；开发Vite代理另有固定端口配置。','普通模式可选CPU/MPS/CUDA中的可用设备；重任务在独立进程执行。'],nodes=['delivery-source','fe-app','be-api','be-runtime','be-training','be-inference'],storage=[dict(location='var/ 或 SONGCUT_DATA',content='用户项目、素材、模型和导出',owner='本地用户；不会提交到Git'),dict(location='localStorage',content='当前站点的外观偏好',owner='浏览器；换域名/端口各自隔离')],limits=['本地正常模式保留模型训练。','实际使用8777时，Vite开发代理当前仍需调整。','运行数据与代码目录分开。']),
 dict(id='server',title='Linux 低配服务器',summary='独立录播器负责保存直播，拾歌负责入库、共享预览和受控计算；Caddy可提供域名入口。',steps=['deploy.sh --recorder启动Compose并自动设置录播回调。','BililiveRecorder保存原始流，FileClosed通知拾歌。','应用把完成文件登记为共享项目；按队列生成320p。','浏览器使用私人草稿编辑，按配额排队检测和导出。'],nodes=['delivery-entry','delivery-server','external-recorder','be-recorder','be-jobs','be-access','be-resource-policy'],storage=[dict(location='recorder-data → /recordings:ro',content='完整原始录播',owner='录播姬写入；拾歌只读引用'),dict(location='songcut-data → /data',content='SQLite、模型、共享衍生文件、个人任务产物',owner='拾歌服务'),dict(location='server-accounts.json / 服务端环境',content='账户、角色及私有配置',owner='管理员；前端不接收密码'),dict(location='/data/previews / detections',content='可复用预览与检测缓存',owner='应用；引用/任务状态决定清理资格')],limits=['默认一个重计算执行位；每账户与总队列有上限。','低配模式关闭训练，CPU分块推理。','上传/媒体连接/带宽与磁盘余量受控。','当前为受控FileResponse+Caddy反代，没有HLS/CDN/对象存储。']),
 dict(id='desktop',title='Tauri 桌面应用',summary='Rust壳负责WebView与内置后端生命周期，复用同一网页和Python业务代码。',steps=['Tauri寻找或启动可信的本机拾歌后端。','默认端口被其他程序占用时选择可用回环端口。','校验自己的进程、端口就绪信号和健康身份后打开WebView。','退出只清理自己启动的后端；打包模式从资源目录定位工具和worker。'],nodes=['delivery-desktop','delivery-frozen','be-runtime','fe-app','be-api'],storage=[dict(location='系统应用数据目录',content='项目、模型、录播与导出',owner='当前系统用户'),dict(location='安装包 backend/_internal/resources',content='Python/ML、FFmpeg、字体与静态UI',owner='发行构建；不是可以随意删除的临时缓存')],limits=['桌面宿主不重新实现剪辑/训练业务。','当前源码改动不自动等于已发布安装包更新。','各系统签名、依赖和安装验收是独立交付工作。'])]
c=de['contract_audit']
manifest=dict(storageCatalog=[dict(location=s['path'],content=s['purpose'],owner=' / '.join(byid[i]['title'] for i in s.get('owners',[]) if i in byid),retention=s.get('retention',''),tables=[f'{name} → {owner}' for name,owner in s.get('tables',{}).items()]) for s in be['storage']],nodes=nodes,edges=edges,features=features,plans=plans,findings=findings,runtimes=runtimes,overview=[['fe-app','fe-project-flow','fe-training','fe-recordings'],['fe-api','be-api','be-recorder-api','be-access'],['be-studio','be-jobs','be-training','be-domain'],['be-media','be-inference','be-store','be-recorder']],contract=dict(snapshotPaths=c['saved_paths'],snapshotOperations=c['saved_operations'],sourcePaths=c['source_paths'],sourceOperations=c['source_operations'],missingOperations=c['missing_operations'],missingProjectFields=c['missing_project_fields']))
(HERE/'content.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print(f'{len(nodes)} nodes, {len(edges)} edges, {len(features)} features, {len(plans)} plans')
