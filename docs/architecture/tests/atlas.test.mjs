import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const ts=require('../../../apps/web/node_modules/typescript');
const context={window:{}};
vm.runInNewContext(fs.readFileSync(new URL('../data.js',import.meta.url),'utf8'),context);
const data=JSON.parse(JSON.stringify(context.window.SONGCUT_ARCHITECTURE));
const {outputText}=ts.transpileModule(fs.readFileSync(new URL('../src/model.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});
const {ArchitectureRepository,ExplorerStore}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const repository=new ArchitectureRepository(data);
test('all source anchors, workflow nodes and test references resolve in the source snapshot',()=>{
 const files=new Map(data.files.map(f=>[f.path,f]));
 function walk(value){
  if(Array.isArray(value))return value.forEach(walk);
  if(!value||typeof value!=='object')return;
  if(value.path&&typeof value.line==='number'){assert.ok(files.has(value.path),value.path);assert.ok(value.line>=1&&value.line<=files.get(value.path).lines);}
  Object.values(value).forEach(walk);
 }
 walk(data.nodes);walk(data.features);walk(data.plans);
 for(const feature of data.features){for(const step of feature.steps)assert.ok(repository.node(step.node));for(const p of feature.tests)assert.ok(files.has(p));}
 assert.ok(data.features.some(f=>f.status==='interface'));
 assert.ok(data.nodes.some(n=>n.status==='planned'));
});
test('Chinese features and real symbols find their implementation files',()=>{
 assert.ok(repository.searchFiles('320p','').some(f=>f.path==='apps/web/src/playback.ts'));
 assert.ok(repository.searchFiles('JobRunner','Python').some(f=>f.path==='services/studio/studio/jobs.py'));
 assert.equal(repository.searchFiles('this-does-not-exist','').length,0);
 assert.ok(repository.sourceUrl({path:'apps/web/src/main.tsx',line:122}).endsWith('/apps/web/src/main.tsx#L122'));
});
test('planning remains local and exported suggestions do not claim executed refactors',()=>{
 const storage=new Map();globalThis.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
 globalThis.location={hash:'#view=plan'};globalThis.history={replaceState(){}};
 const store=new ExplorerStore(repository);
 let updates=0;const stop=store.subscribe(()=>updates++);
 const id=data.plans[0].id;store.togglePlan(id);
 assert.equal(store.snapshot.selectedPlans[0],id);
 assert.equal(JSON.parse(store.exportPlan()).items[0].id,id);
 assert.equal(JSON.parse(store.exportPlan()).scope,'建议，未执行重构');
 const next=new ExplorerStore(repository);assert.equal(next.snapshot.selectedPlans[0],id);
 store.togglePlan(id);assert.equal(store.snapshot.selectedPlans.length,0);assert.equal(updates,2);stop();
});
test('invalid local settings and unknown module references are handled deliberately',()=>{
 globalThis.localStorage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
 globalThis.location={hash:'#view=garbage&node=absent'};globalThis.history={replaceState(){}};
 const store=new ExplorerStore(repository);assert.equal(store.snapshot.view,'overview');store.togglePlan('unknown');assert.equal(store.snapshot.selectedPlans.length,0);
 assert.throws(()=>new ArchitectureRepository({...data,edges:[{from:'absent',to:'also-absent'}]}),/Unknown dependency/);
});
test('unimplemented remote execution and real protocol boundaries stay distinct',()=>{
 const remote=data.features.find(f=>f.title.includes('远程Mac'));
 const recognition=data.features.find(f=>f.title.includes('自动歌名'));
 assert.equal(remote.status,'planned');assert.equal(recognition.status,'interface');
 const training=data.features.find(f=>f.id==='training');
 assert.ok(training.steps.some(s=>s.node==='be-training'));
 assert.ok(!training.steps.some(s=>s.node==='be-jobs'));
 assert.equal(data.routes.length,data.contract.sourceOperations);
 assert.ok(data.storageCatalog.some(s=>s.tables.some(t=>t.startsWith('training_jobs'))));
});
