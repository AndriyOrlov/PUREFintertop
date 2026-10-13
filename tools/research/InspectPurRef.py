# Ghidra Jython: targeted decompilation for file interoperability research.
# @category PureRef
import os,json
from ghidra.app.decompiler import DecompInterface
args=getScriptArgs();out=args[0];targets=json.load(open(os.path.join(out,'targets.json')))
interface=DecompInterface();interface.openProgram(currentProgram)
summary=[]
if targets:
 for n,target in enumerate(targets):
  address=toAddr(target['address'])
  function=getFunctionAt(address)
  if function is None:
   # PIE images may be rebased by the loader.
   for offset in [0x100000,0x10000]:
    candidate=toAddr(hex(int(target['address'],16)+offset))
    possible=getFunctionAt(candidate)
    if possible is not None:function=possible;address=candidate;break
  if function is None:
   disassemble(address);function=createFunction(address,'pur_target_'+str(n))
  if function is None:
   summary.append({'name':target['name'],'status':'no function'});continue
  disassemble(function.getEntryPoint())
  result=interface.decompileFunction(function,45,monitor)
  if result.decompileCompleted():
   path=os.path.join(out,'decompiled-'+str(n)+'.c')
   with open(path,'w') as stream:stream.write('// '+target['name']+'\n'+result.getDecompiledFunction().getC())
   summary.append({'name':target['name'],'address':str(function.getEntryPoint()),'status':'decompiled','file':os.path.basename(path)})
  else:summary.append({'name':target['name'],'status':result.getErrorMessage()})
else:
 # Stripped 1.x builds: locate functions that reference file-format markers.
 markers=['GraphicsImageItem','GraphicsTextItem','1.10','Could not open file','checksum']
 seen=set()
 for datum in currentProgram.getListing().getDefinedData(True):
  value=datum.getValue()
  if value is None:continue
  text=str(value)
  if not any(marker in text for marker in markers):continue
  for reference in getReferencesTo(datum.getAddress()):
   function=getFunctionContaining(reference.getFromAddress())
   if function is None or str(function.getEntryPoint()) in seen:continue
   seen.add(str(function.getEntryPoint()));result=interface.decompileFunction(function,45,monitor)
   if result.decompileCompleted():
    path=os.path.join(out,'decompiled-'+str(len(summary))+'.c')
    with open(path,'w') as stream:stream.write(result.getDecompiledFunction().getC())
    summary.append({'name':function.getName(),'address':str(function.getEntryPoint()),'marker':text[:120],'status':'decompiled','file':os.path.basename(path)})
json.dump(summary,open(os.path.join(out,'decompilation-summary.json'),'w'),indent=2)
print('PUREF_RESEARCH',len(summary),'functions inspected')
interface.dispose()
