"""Extract Python symbols/routes without importing the application or touching its data."""
import ast,json,sys
items=json.load(sys.stdin)
result={}
for path,source in items.items():
 if not path.endswith('.py'):continue
 tree=ast.parse(source);lines=source.splitlines();symbols=[];imports=[];routes=[];fields={}
 def walk(node,prefix=''):
  for child in ast.iter_child_nodes(node):
   if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
    name=f'{prefix}.{child.name}' if prefix else child.name
    if isinstance(child,ast.ClassDef):fields[name]=[n.target.id for n in child.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)]
    symbols.append(dict(name=name,kind='class' if isinstance(child,ast.ClassDef) else 'method' if isinstance(node,ast.ClassDef) else 'function',line=child.lineno,snippet='\n'.join(lines[child.lineno-1:min(child.end_lineno,child.lineno+11)])))
    for decorator in child.decorator_list:
     if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr in ('get','post','put','patch','delete','head','options') and decorator.args and isinstance(decorator.args[0],ast.Constant) and isinstance(decorator.args[0].value,str):
      routes.append(dict(method=decorator.func.attr.upper(),path=decorator.args[0].value,line=child.lineno,symbol=name))
    walk(child,name)
   else:walk(child,prefix)
 walk(tree)
 for node in ast.walk(tree):
  if isinstance(node,ast.Import):imports.extend(x.name for x in node.names)
  if isinstance(node,ast.ImportFrom):imports.append('.'*node.level+(node.module or '')+' → '+', '.join(x.name for x in node.names))
 result[path]=dict(symbols=symbols,imports=sorted(set(imports)),routes=routes,fields=fields)
print(json.dumps(result,ensure_ascii=False))
