import { ArchitectureRepository, type ExplorerState, type SourceRef, type AtlasNode, type View, statusNames, layerNames } from './model';
const esc=(value:unknown)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const list=(values:string[])=>`<ul>${values.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
const action=(name:string,id:string,label:string,cls='')=>`<button type="button" class="${cls}" data-action="${name}" data-id="${esc(id)}">${label}</button>`;
const tabs:{id:View;label:string;sub:string}[]=[{id:'overview',label:'架构总览',sub:'模块与依赖'},{id:'features',label:'功能链路',sub:'从操作到代码'},{id:'files',label:'文件地图',sub:'类、函数与源码'},{id:'plan',label:'重构规划',sub:'UI 与业务分离'},{id:'runtime',label:'部署与数据',sub:'进程与存储边界'}];

/** Presentation only: consumes an immutable snapshot and emits data-action events. */
export class ArchitectureView {
  private observer:ResizeObserver;
  private state!:Readonly<ExplorerState>;
  constructor(private readonly root:HTMLElement,private readonly repo:ArchitectureRepository) {
    this.observer=new ResizeObserver(()=>this.drawEdges());
    this.observer.observe(root);
  }
  private badge(status:AtlasNode['status']) { return `<span class="badge ${status}"><i></i>${statusNames[status]}</span>`; }
  private source(ref:SourceRef,short=false) { return `<div class="source-ref">${action('file',ref.path,`<code>${esc(short?ref.path.split('/').pop():ref.path)}</code><small>${esc(ref.symbol||'')} · L${ref.line}</small>`,'source-button')}<a href="${this.repo.sourceUrl(ref)}" target="_blank" rel="noreferrer" aria-label="查看${esc(ref.path)}第${ref.line}行源码">↗</a></div>`; }
  render(state:Readonly<ExplorerState>) {
    this.state=state;
    const input=document.activeElement as HTMLInputElement;
    const focusId=input?.id,selection=input?.tagName==='INPUT'?[input.selectionStart,input.selectionEnd]:null;
    const focusAction=input?.dataset?.action,focusKey=input?.dataset?.id;
    const d=this.repo.data;
    this.root.innerHTML=`<header class="masthead"><div class="brand"><span class="brand-mark">拾</span><div><strong>拾歌 <span>工程地图</span></strong><small>VTuber SongCut / Architecture atlas</small></div></div><div class="snapshot"><span class="live-dot"></span>源码快照 <code>${d.meta.revision.slice(0,7)}</code><span>${d.meta.date}</span></div></header>
    <div class="navigation"><nav role="tablist" aria-label="架构视图">${tabs.map(t=>`<button type="button" id="tab-${t.id}" role="tab" aria-selected="${state.view===t.id}" aria-controls="content" tabindex="${state.view===t.id?0:-1}" data-action="view" data-id="${t.id}" class="${state.view===t.id?'active':''}"><strong>${t.label}</strong><small>${t.sub}</small></button>`).join('')}</nav><label class="search-label"><span>⌕</span><input id="global-search" type="search" placeholder="搜索功能、文件或类…" value="${esc(state.query)}" aria-label="搜索功能、文件或类"></label></div>
    <main id="content" role="tabpanel" aria-labelledby="tab-${state.view}" tabindex="-1">${state.view==='overview'?this.overview():state.view==='features'?this.features():state.view==='files'?this.files():state.view==='plan'?this.plan():this.runtime()}</main>
    <footer><span>符号由AST提取 · 关系经人工审阅 · 建议尚未执行</span><span>${esc(d.meta.branch)}${d.meta.sourceDirty?' · 含未提交业务源码':''}</span><a href="${d.meta.repo}/tree/${d.meta.revision}" target="_blank" rel="noreferrer">查看此版仓库 ↗</a></footer>`;
    if(focusId){const el=document.getElementById(focusId) as HTMLInputElement|null;if(el){el.focus({preventScroll:true});if(selection&&el.type==='search')el.setSelectionRange(selection[0],selection[1]);}}
    if(!focusId&&focusAction&&focusKey!==undefined){this.root.querySelector<HTMLElement>(`[data-action="${CSS.escape(focusAction)}"][data-id="${CSS.escape(focusKey)}"]`)?.focus({preventScroll:true});}
    requestAnimationFrame(()=>this.drawEdges());
  }
  private title(eyebrow:string,title:string,description:string) { return `<div class="section-title"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${description}</p></div></div>`; }
  private overview() {
    const d=this.repo.data,s=this.state;
    const selected=this.repo.node(s.node)||d.nodes[0];
    const groups=d.overview.map(x=>[...x]);
    if(!groups.flat().includes(selected.id))groups[selected.layer==='ui'||selected.layer==='ts'?0:selected.layer==='api'?1:selected.layer==='core'?2:3].push(selected.id);
    const counts={ts:d.files.filter(f=>['TypeScript','TSX'].includes(f.language)&&f.category==='source').length,py:d.files.filter(f=>f.language==='Python'&&f.category==='source').length};
    return this.title('CURRENT SYSTEM / 当前实现','从录播，到一段可以发布的歌声','浏览器和桌面共用一套工作台；Python 服务协调媒体、模型与持久化。点击模块查看上下游和源码。')+
    `<div class="facts"><div><b>React + TypeScript</b><span>当前视图框架；未使用 Vue</span></div><div><b>FastAPI + SQLite</b><span>模块化单体与独立计算进程</span></div><div><b>${counts.ts} TS / ${counts.py} Python</b><span>业务源文件 · 不含测试和构建脚本</span></div><div class="warm"><b>${d.contract.missingOperations.length} 个契约缺项</b><span>静态 OpenAPI 快照需要同步</span></div></div>
    <div class="split"><section class="panel architecture"><div class="panel-heading"><h2>模块关系 <small class="scroll-hint">可横向滚动</small></h2><span>选中：${esc(selected.title)}</span></div><div class="graph-scroll"><div class="graph-board"><svg class="graph-edges" aria-hidden="true"></svg><div class="graph-columns">${groups.map((ids,i)=>`<div class="graph-column"><div class="column-label"><em>${String(i+1).padStart(2,'0')}</em>${['交互与前端逻辑','请求与访问边界','用例与任务协调','执行、依赖与存储'][i]}</div>${ids.map(id=>this.nodeCard(this.repo.node(id)!)).join('')}</div>`).join('')}</div></div></div><div class="graph-legend"><span><i class="line"></i>实际依赖／调用</span><span><i class="line dashed"></i>预留或规划</span><span>当前只展开所选模块的直接关系</span></div></section>${this.inspector(selected)}</div>
    <section class="panel catalog"><div class="panel-heading"><h2>细分模块 <small>${d.nodes.length}</small></h2><div class="filters"><select id="layer-filter" aria-label="模块层级"><option value="">全部层级</option>${Object.entries(layerNames).map(([id,name])=>`<option value="${id}" ${s.layer===id?'selected':''}>${name}</option>`).join('')}</select><select id="status-filter" aria-label="实现状态"><option value="">全部状态</option>${Object.entries(statusNames).map(([id,name])=>`<option value="${id}" ${s.status===id?'selected':''}>${name}</option>`).join('')}</select></div></div><div class="module-grid">${d.nodes.filter(n=>(!s.layer||n.layer===s.layer)&&(!s.status||n.status===s.status)).map(n=>action('node',n.id,`<span class="module-title">${esc(n.title)}${this.badge(n.status)}</span><p>${esc(n.summary)}</p><small>${esc(layerNames[n.layer])} · ${esc(n.tech)}</small>`,n.id===selected.id?'module-card selected':'module-card')).join('')}</div></section>`;
  }
  private nodeCard(n:AtlasNode) {
    const s=this.state;const related=n.id===s.node||this.repo.dependencies(s.node).some(e=>e.to===n.id)||this.repo.consumers(s.node).some(e=>e.from===n.id);
    return `<button type="button" class="graph-node ${n.id===s.node?'selected':''} ${related?'related':''} ${n.status}" data-node="${n.id}" data-action="node" data-id="${n.id}" aria-pressed="${n.id===s.node}"><small>${esc(n.tech)}</small><strong>${esc(n.title)}</strong><code>${esc(n.status==='planned'?'建议边界 · 尚未实现':(['ui','ts'].includes(n.layer)?n.sources[0]?.path.split('/').pop():n.sources[0]?.symbol)||'外部组件')}</code></button>`;
  }
  private inspector(n:AtlasNode) {
    const incoming=this.repo.consumers(n.id),outgoing=this.repo.dependencies(n.id);
    return `<aside class="panel inspector" tabindex="-1" aria-label="选中模块详情"><p class="eyebrow">MODULE DETAIL</p><div class="inspector-title"><h2>${esc(n.title)}</h2>${this.badge(n.status)}</div><p class="lead">${esc(n.summary)}</p><h3>承担什么</h3>${list(n.responsibilities)}<h3>${n.status==='planned'?'当前相关代码（非目标实现）':'对应代码'}</h3>${n.sources.map(s=>this.source(s,true)).join('')||'<p class="muted">外部运行时；具体版本见依赖清单。</p>'}<h3>上游 / 使用方</h3><div class="relations">${incoming.map(e=>action('node',e.from,`${esc(this.repo.node(e.from)?.title)} <small>${esc(e.label)}</small>`)).join('')||'<span class="muted">入口／暂无业务接入</span>'}</div><h3>它依赖谁</h3><div class="relations">${outgoing.map(e=>action('node',e.to,`${esc(this.repo.node(e.to)?.title)} <small>${esc(e.label)}</small>`)).join('')||'<span class="muted">无本图内直接依赖</span>'}</div>${n.risks.length?`<div class="boundary"><h3>边界与注意事项</h3>${list(n.risks)}</div>`:''}</aside>`;
  }
  private features() {
    const d=this.repo.data,s=this.state,feature=d.features.find(f=>f.id===s.feature)||d.features[0];
    const index=Math.min(s.step,Math.max(0,feature.steps.length-1)),step=feature.steps[index],node=step&&this.repo.node(step.node);
    return this.title('FEATURE TRACE / 功能到代码','一次操作，经过哪些组件？','选择功能，再点任意步骤。实际执行与仅有接口的能力分别标记，不把接口定义当成成品功能。')+
    `<div class="feature-layout"><aside class="panel feature-menu">${d.features.map(f=>action('feature',f.id,`<span>${esc(f.title)}</span>${this.badge(f.status)}`,feature.id===f.id?'active':'')).join('')}</aside><div><section class="panel flow"><div class="flow-title"><h2>${esc(feature.title)}</h2>${this.badge(feature.status)}</div><p class="lead">${esc(feature.description)}</p><ol class="flow-steps">${feature.steps.map((x,i)=>`<li>${action('step',String(i),`<b>${String(i+1).padStart(2,'0')}</b><span>${esc(x.title)}</span><small>${esc(this.repo.node(x.node)?.title||'')}</small>`,i===index?'active':'')}</li>`).join('')}</ol>${step?`<div class="step-detail"><div><span class="eyebrow">STEP ${index+1}</span><h3>${esc(step.title)}</h3><p>${esc(step.detail)}</p>${step.source?this.source(step.source):''}</div>${node?action('node',node.id,'查看这个模块 ↗','button'):''}</div>`:''}<div class="flow-bottom"><div><h3>产物与数据变化</h3>${list(feature.outputs)}</div><div><h3>使用边界</h3>${list(feature.caveats)}</div></div><details><summary>对应验证文件（${feature.tests.length}）</summary>${feature.tests.map(path=>this.source({path,line:1},false)).join('')}</details></section></div></div>`;
  }
  private files() {
    const s=this.state,d=this.repo.data,files=this.repo.searchFiles(s.query,s.language);
    const file=files.find(f=>f.path===s.file)||files[0];
    const modules=file?d.nodes.filter(n=>n.sources.some(x=>x.path===file.path)):[];
    return this.title('SOURCE INDEX / 源码索引','找到功能，也找到实现位置','搜索中文功能名、文件路径或类／函数名。符号与行号从当前源文件生成，源码链接固定到分析版本。')+
    `<div class="file-toolbar"><span aria-live="polite">${files.length} 个匹配文件</span><label>语言 <select id="language-filter"><option value="">全部语言</option>${[...new Set(d.files.map(f=>f.language))].sort().map(lang=>`<option ${lang===s.language?'selected':''}>${lang}</option>`).join('')}</select></label>${action('clear','','清除筛选','button')}</div><div class="file-layout"><section class="panel file-list">${files.map(f=>action('file',f.path,`<span>${esc(f.path)}</span><small>${esc(f.language)} · ${f.lines} 行</small>`,file?.path===f.path?'active':'')).join('')||'<div class="empty">没有匹配文件。可搜索“预览”“模型”或“Project”。</div>'}</section><section class="panel file-detail">${file?`<div class="flow-title"><div><p class="eyebrow">${esc(file.language)} / ${esc(file.category)}</p><h2>${esc(file.path.split('/').pop())}</h2></div><span class="file-lines">${file.lines} 行</span></div><p class="file-path">${esc(file.path)}</p>${modules.map(n=>`<p>${esc(n.summary)} ${action('node',n.id,'模块关系 ↗','text-button')}</p>`).join('')}<h3>符号与实现</h3>${file.symbols.length?file.symbols.map(symbol=>`<details class="symbol"><summary><code>${esc(symbol.name)}</code><span>${esc(symbol.kind)} · L${symbol.line}</span></summary><a href="${this.repo.sourceUrl({path:file.path,line:symbol.line})}" target="_blank" rel="noreferrer">在源码中打开 ↗</a><pre><code>${esc(symbol.snippet)}</code></pre></details>`).join(''):'<p class="muted">配置／说明文件，未提取语言符号。</p>'}<details class="imports"><summary>导入与引用（${file.imports.length}）</summary>${list(file.imports)}</details><a class="button" href="${this.repo.sourceUrl({path:file.path,line:1})}" target="_blank" rel="noreferrer">查看完整文件 ↗</a>`:'<div class="empty">选择左侧文件查看细节。</div>'}</section></div>`;
  }
  private plan() {
    const d=this.repo.data,s=this.state;
    return this.title('REFACTOR BLUEPRINT / 重构蓝图','让 UI 变薄，让逻辑可以复用','下面是建议结构，尚未重构业务代码。有状态流程用 TS 类管理；时间换算、校验和数据变换继续复用纯函数。')+
    `<section class="panel blueprint"><div><span class="badge planned">目标结构 · 尚未实现</span><h2>React / Vue 只做视图</h2><p>今天是 React，未来若采用 Vue，也由组件读取状态、转发操作。领域规则与请求生命周期留在独立 TypeScript 层；训练、媒体处理与最终权限校验仍由 Python 服务负责。</p></div><div class="blueprint-chain"><span>React / Vue 视图<small>渲染 · 绑定 · 事件</small></span><b>→</b><span>TS 应用服务类<small>Session / Coordinator</small></span><b>→</b><span>端口 / Repository<small>HttpClient · DTO</small></span><b>→</b><span>Python 服务<small>任务 · 存储 · 执行</small></span></div></section>
    <div class="plan-toolbar"><span>选择希望优先整理的项 · 仅保存在当前浏览器</span><button type="button" class="button primary" data-action="export" ${s.selectedPlans.length?'':'disabled'}>导出计划（${s.selectedPlans.length}）</button></div>
    <div class="plan-grid">${d.plans.map(p=>`<article class="panel plan-card"><div class="plan-head"><span class="priority ${p.priority}">${p.priority}</span><button type="button" class="plan-toggle ${s.selectedPlans.includes(p.id)?'checked':''}" data-action="plan" data-id="${p.id}" aria-pressed="${s.selectedPlans.includes(p.id)}">${s.selectedPlans.includes(p.id)?'✓ 已加入计划':'+ 加入计划'}</button></div><h2>${esc(p.title)}</h2><p>${esc(p.problem)}</p><div class="target"><b>建议边界</b><p>${esc(p.target)}</p><div class="class-chips">${p.classes.map(c=>`<code>${esc(c)}</code>`).join('')}</div></div><h3>保持与复用</h3>${list(p.preserve)}<details><summary>完成判据与当前代码</summary>${list(p.acceptance)}${p.sources.map(x=>this.source(x,true)).join('')}</details></article>`).join('')}</div>
    <section class="panel findings"><h2>需要先看清的现状</h2>${d.findings.map(f=>`<article><h3>${esc(f.title)}</h3><p>${esc(f.body)}</p>${f.sources.map(x=>this.source(x,true)).join('')}</article>`).join('')}</section>`;
  }
  private runtime() {
    const d=this.repo.data,r=d.runtimes.find(r=>r.id===this.state.runtime)||d.runtimes[0];
    return this.title('RUNTIME / 部署与数据','同一套工作流，三种运行方式','区分浏览器、应用服务、计算子进程与外部录制器。下面描述分支源码的能力，不表示旧安装包已经包含这些改动。')+
    `<div class="runtime-tabs">${d.runtimes.map(x=>action('runtime',x.id,esc(x.title),x.id===r.id?'button active':'button')).join('')}</div><div class="runtime-layout"><section class="panel runtime-main"><h2>${esc(r.title)}</h2><p class="lead">${esc(r.summary)}</p><ol class="runtime-steps">${r.steps.map((x,i)=>`<li><span>${i+1}</span><p>${esc(x)}</p></li>`).join('')}</ol><h3>参与模块</h3><div class="relations horizontal">${r.nodes.filter(id=>this.repo.node(id)).map(id=>action('node',id,esc(this.repo.node(id)!.title))).join('')}</div><h3>约束与保护</h3>${list(r.limits)}</section><section class="panel storage"><h2>数据归属</h2><p class="muted">原片与模型是资产；临时数据不能按文件名混删。</p>${r.storage.map(x=>`<article><code>${esc(x.location)}</code><strong>${esc(x.content)}</strong><span>${esc(x.owner)}</span></article>`).join('')}</section></div>
    <section class="panel contract"><div><p class="eyebrow">CONTRACT AUDIT</p><h2>接口快照与实现并未完全同步</h2><p>静态快照：${d.contract.snapshotPaths} 个路径 / ${d.contract.snapshotOperations} 个操作；源码路由：${d.contract.sourcePaths} 个路径 / ${d.contract.sourceOperations} 个操作。框架自动文档路由不计入此处。</p></div><details><summary>展开缺失的操作与 Project 字段</summary><div class="flow-bottom"><div><h3>未进入静态快照的操作</h3>${list(d.contract.missingOperations)}</div><div><h3>缺少的领域字段</h3>${list(d.contract.missingProjectFields)}</div></div><p>动态 dict 返回值还需补明确 DTO，单纯重新导出 OpenAPI 不能自动解决全部 TS 类型问题。</p></details></section>
    <section class="panel contract"><details><summary>全部 HTTP 接口（${d.routes.length}） · 只查看代码，不执行请求</summary><div class="route-scroll"><table class="route-table"><thead><tr><th>方法</th><th>路径</th><th>处理函数与源码</th></tr></thead><tbody>${d.routes.map(route=>`<tr><td><code>${esc(route.method)}</code></td><td><code>${esc(route.path)}</code></td><td>${this.source({path:route.file,line:route.line,symbol:route.symbol},true)}</td></tr>`).join('')}</tbody></table></div></details></section>
    <section class="panel contract"><details><summary>细分数据目录与表（${d.storageCatalog.length}）</summary><div class="storage-grid">${d.storageCatalog.map(item=>`<article><code>${esc(item.location)}</code><h3>${esc(item.content)}</h3><p>${esc(item.owner)}</p><p>${esc(item.retention)}</p>${item.tables.length?list(item.tables):''}</article>`).join('')}</div></details></section>`;
  }
  private drawEdges() {
    const board=this.root.querySelector<HTMLElement>('.graph-board'),svg=this.root.querySelector<SVGElement>('.graph-edges');
    if(!board||!svg)return;
    const rect=board.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${rect.width} ${rect.height}`);
    const edges=this.repo.data.edges.filter(e=>e.from===this.state.node||e.to===this.state.node);
    let content='<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="currentColor"/></marker></defs>';
    for(const edge of edges){
      const from=this.root.querySelector<HTMLElement>(`[data-node="${CSS.escape(edge.from)}"]`),to=this.root.querySelector<HTMLElement>(`[data-node="${CSS.escape(edge.to)}"]`);
      if(!from||!to)continue;
      const a=from.getBoundingClientRect(),b=to.getBoundingClientRect(),forward=b.left>a.left;
      const x1=(forward?a.right:a.left)-rect.left,y1=a.top+a.height/2-rect.top,x2=(forward?b.left:b.right)-rect.left,y2=b.top+b.height/2-rect.top;
      let route:string;
      if(Math.abs(a.left-b.left)<2){
        if(Math.abs(a.top-b.top)<Math.max(a.height,b.height)+40){
          const x=a.left+a.width*.6-rect.left,down=b.top>a.top;
          route=`M${x},${(down?a.bottom:a.top)-rect.top} L${x},${(down?b.top:b.bottom)-rect.top}`;
        }else{
          const outside=Math.max(a.right,b.right)-rect.left+10;
          route=`M${a.right-rect.left},${y1} H${outside} V${y2} H${b.right-rect.left}`;
        }
      }else if(Math.abs(x2-x1)<80){
        const middle=(x1+x2)/2;
        route=`M${x1},${y1} H${middle} V${y2} H${x2}`;
      }else{
        const sign=forward?1:-1;
        const top=Math.min(...[...this.root.querySelectorAll<HTMLElement>('[data-node]')].map(n=>n.getBoundingClientRect().top))-rect.top-7;
        route=`M${x1},${y1} H${x1+sign*10} V${top} H${x2-sign*10} V${y2} H${x2}`;
      }
      content+=`<path class="edge ${edge.kind==='planned'?'planned':''}" d="${route}" marker-end="url(#arrow)"><title>${esc(edge.label)}</title></path>`;
    }
    svg.innerHTML=content;
  }
}
