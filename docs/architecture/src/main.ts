import { ArchitectureRepository, ExplorerStore, type AtlasData, type View } from './model';
import { ArchitectureView } from './view';
declare global { interface Window { SONGCUT_ARCHITECTURE:AtlasData } }

/** Binds UI events to the repository/store; the view only renders snapshots. */
class AtlasController {
  private readonly repository=new ArchitectureRepository(window.SONGCUT_ARCHITECTURE);
  private readonly store=new ExplorerStore(this.repository);
  private readonly view:ArchitectureView;
  constructor(root:HTMLElement) {
    this.view=new ArchitectureView(root,this.repository);
    this.store.subscribe(()=>this.view.render(this.store.snapshot));
    root.addEventListener('click',event=>this.click(event));
    root.addEventListener('input',event=>{
      const el=event.target as HTMLInputElement;
      if(el.id==='global-search'){this.store.update({query:el.value,view:'files'});window.scrollTo({top:0});}
    });
    root.addEventListener('change',event=>{
      const el=event.target as HTMLSelectElement;
      if(el.id==='language-filter')this.store.update({language:el.value});
      if(el.id==='status-filter')this.store.update({status:el.value});
      if(el.id==='layer-filter')this.store.update({layer:el.value});
    });
    root.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&this.store.snapshot.query)this.store.update({query:''});
      if(event.target instanceof HTMLElement&&event.target.getAttribute('role')==='tab'&&['ArrowLeft','ArrowRight'].includes(event.key)){
        const tabs=[...root.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
        const index=tabs.indexOf(event.target as HTMLButtonElement);
        const next=tabs[(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length];
        event.preventDefault();next.click();document.getElementById(next.id)?.focus();
      }
    });
    this.view.render(this.store.snapshot);
  }
  private click(event:MouseEvent) {
    const button=(event.target as Element).closest<HTMLElement>('[data-action]');
    if(!button)return;
    const id=button.dataset.id||'';
    switch(button.dataset.action){
      case 'view':this.store.update({view:id as View,query:''});window.scrollTo({top:0});break;
      case 'node':{this.store.update({node:id,view:'overview',layer:'',status:''});if(innerWidth<=1100){const detail=document.querySelector<HTMLElement>('.inspector');detail?.scrollIntoView({block:'start'});detail?.focus({preventScroll:true});}break;}
      case 'feature':this.store.update({feature:id,view:'features',step:0,query:''});window.scrollTo({top:0});break;
      case 'file':{this.store.update({file:id,view:'files',...(this.store.snapshot.view==='files'?{}:{query:'',language:''})});if(innerWidth<=760)document.querySelector('.file-detail')?.scrollIntoView({block:'start'});else window.scrollTo({top:0});break;}
      case 'step':this.store.update({step:Number(id)});break;
      case 'plan':this.store.togglePlan(id);break;
      case 'runtime':this.store.update({runtime:id});break;
      case 'clear':this.store.update({query:'',language:'',status:'',layer:''});break;
      case 'export':{
        const blob=new Blob([this.store.exportPlan()],{type:'application/json'});
        const url=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=url;a.download='songcut-refactor-plan.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);break;
      }
    }
  }
}
new AtlasController(document.getElementById('app')!);
