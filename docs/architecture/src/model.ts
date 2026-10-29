export type Status = 'implemented' | 'interface' | 'planned' | 'external' | 'partial';
export type View = 'overview' | 'features' | 'files' | 'plan' | 'runtime';
export interface SourceRef { path: string; line: number; symbol?: string }
export interface AtlasNode { id: string; title: string; layer: string; status: Status; summary: string; responsibilities: string[]; sources: SourceRef[]; risks: string[]; tech: string }
export interface Edge { from: string; to: string; label: string; kind?: 'data' | 'control' | 'planned' }
export interface Feature { id: string; title: string; status: Status; description: string; steps: {title: string; node: string; detail: string; source?: SourceRef}[]; outputs: string[]; caveats: string[]; tests: string[] }
export interface SourceFile { path: string; language: string; lines: number; symbols: {name:string; kind:string; line:number; snippet:string}[]; imports:string[]; category:string }
export interface Refactor { id:string; priority:string; title:string; problem:string; target:string; classes:string[]; preserve:string[]; sources:SourceRef[]; acceptance:string[] }
export interface Runtime { id:string; title:string; summary:string; steps:string[]; nodes:string[]; storage:{location:string;content:string;owner:string}[]; limits:string[] }
export interface AtlasData { routes:{method:string;path:string;file:string;line:number;symbol:string}[]; storageCatalog:{location:string;content:string;owner:string;retention:string;tables:string[]}[]; overview:string[][]; meta:{revision:string;date:string;branch:string;sourceDirty:boolean;repo:string}; nodes:AtlasNode[]; edges:Edge[]; features:Feature[]; files:SourceFile[]; plans:Refactor[]; runtimes:Runtime[]; findings:{title:string;body:string;sources:SourceRef[]}[]; contract:{snapshotPaths:number;sourcePaths:number;snapshotOperations:number;sourceOperations:number;missingOperations:string[];missingProjectFields:string[]} }
export const statusNames: Record<Status,string> = { implemented:'已实现', interface:'仅接口', planned:'重构建议', external:'外部依赖', partial:'部分接入' };
export const layerNames: Record<string,string> = {ui:'UI 视图', ts:'TS 逻辑', api:'HTTP 边界', core:'应用与领域', runtime:'运行与存储', external:'外部能力', delivery:'桌面与交付'};

/** A framework-independent, read-only repository for the audited source snapshot. */
export class ArchitectureRepository {
  private readonly byId: Map<string,AtlasNode>;
  constructor(readonly data:AtlasData) {
    this.byId = new Map(data.nodes.map(n=>[n.id,n]));
    if (this.byId.size !== data.nodes.length) throw Error('Duplicate architecture node');
    for (const e of data.edges) if (!this.byId.has(e.from)||!this.byId.has(e.to)) throw Error(`Unknown dependency ${e.from}/${e.to}`);
  }
  node(id:string) { return this.byId.get(id); }
  dependencies(id:string) { return this.data.edges.filter(e=>e.from===id); }
  consumers(id:string) { return this.data.edges.filter(e=>e.to===id); }
  file(path:string) { return this.data.files.find(f=>f.path===path); }
  searchFiles(query:string,language:string) {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return this.data.files.filter(file=> {
      if (language && file.language!==language) return false;
      const nodes=this.data.nodes.filter(n=>n.sources.some(s=>s.path===file.path));
      const related=this.data.features.filter(f=>f.tests.includes(file.path)||f.steps.some(s=>s.source?.path===file.path || nodes.some(n=>n.id===s.node)));
      const routeNames=(this.data.routes||[]).filter(r=>r.file===file.path).map(r=>r.method+' '+r.path);
      const haystack=[file.path,...routeNames,...file.imports,...file.symbols.map(s=>s.name),...nodes.map(n=>[n.title,n.summary,...n.responsibilities].join(' ')),...related.map(f=>f.title+' '+f.description)].join(' ').toLowerCase();
      return words.every(word=>haystack.includes(word));
    });
  }
  sourceUrl(source:SourceRef) {
    return `${this.data.meta.repo}/blob/${this.data.meta.revision}/${source.path.split('/').map(encodeURIComponent).join('/')}#L${source.line}`;
  }
}

export interface ExplorerState { view:View; node:string; feature:string; file:string; query:string; language:string; layer:string; status:string; step:number; runtime:string; selectedPlans:string[] }
/** Owns navigation and local planning selections. No dependency on a UI framework. */
export class ExplorerStore {
  private state:ExplorerState;
  private listeners=new Set<()=>void>();
  private readonly storageKey='songcut-architecture-plans-v1';
  constructor(private readonly repository:ArchitectureRepository) {
    let selectedPlans:string[]=[];
    try { const value=JSON.parse(localStorage.getItem(this.storageKey)||'[]'); if(Array.isArray(value)) selectedPlans=value.filter(id=>typeof id==='string'&&repository.data.plans.some(p=>p.id===id)); } catch {}
    this.state={view:'overview',node:'be-studio',feature:'recording',file:'apps/web/src/main.tsx',query:'',language:'',layer:'',status:'',step:0,runtime:'server',selectedPlans};
    const params=new URLSearchParams(location.hash.slice(1));
    const view=params.get('view');
    if(['overview','features','files','plan','runtime'].includes(view||'')) this.state.view=view as View;
    if(repository.node(params.get('node')||''))this.state.node=params.get('node')!;
    if(repository.data.features.some(f=>f.id===params.get('feature')))this.state.feature=params.get('feature')!;
  }
  get snapshot():Readonly<ExplorerState> { return this.state; }
  subscribe(listener:()=>void) { this.listeners.add(listener); return ()=>this.listeners.delete(listener); }
  update(patch:Partial<ExplorerState>) {
    this.state={...this.state,...patch};
    const hash=new URLSearchParams({view:this.state.view,node:this.state.node,feature:this.state.feature});
    history.replaceState(null,'',`#${hash}`);
    this.listeners.forEach(fn=>fn());
  }
  togglePlan(id:string) {
    if(!this.repository.data.plans.some(p=>p.id===id))return;
    const selected=this.state.selectedPlans.includes(id)?this.state.selectedPlans.filter(x=>x!==id):[...this.state.selectedPlans,id];
    try { localStorage.setItem(this.storageKey,JSON.stringify(selected)); } catch {}
    this.update({selectedPlans:selected});
  }
  exportPlan() {
    return JSON.stringify({sourceRevision:this.repository.data.meta.revision,scope:'建议，未执行重构',items:this.repository.data.plans.filter(p=>this.state.selectedPlans.includes(p.id))},null,2);
  }
}
