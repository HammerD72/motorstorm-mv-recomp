"""Compile the production async CPU surface read core and refresh against an authored GPU model, plus mutants."""
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
CORE='/* Asynchronous reads of opted-in surfaces.'
D3D='/* Async copies on the GPU queue. */'
GLUE='/* Async refresh, once per submitted batch. */'
PROMPT='/* Prompt service between batches.'
def section(source,start,stop):
    assert source.count(start)==1,start
    return source[source.index(start):source.index(stop)]
def main():
    source=(ROOT/'vendor/ps3recomp/libs/video/rsx_cpu_surface.inc').read_text()
    out=ROOT/'build/self-tests/cpu-surface-async';out.mkdir(parents=True,exist_ok=True)
    fixture=(ROOT/'scripts/fixtures/cpu_surface_async.c').read_text()
    core=section(source,CORE,D3D);d3d=section(source,D3D,GLUE);glue=section(source,GLUE,PROMPT)
    assert source.index(CORE)>source.index('static void cpu_surface_request('),'async block follows the coherent request'
    # GPU-side wiring the model stands in for.
    issue=d3d[d3d.index('static void cpu_surface_async_issue'):d3d.index('static void cpu_surface_async_pack')]
    assert 'barrier.Transition.StateBefore=r->st;barrier.Transition.StateAfter=D3D12_RESOURCE_STATE_COPY_SOURCE;' in issue
    assert 'barrier.Transition.StateBefore=D3D12_RESOURCE_STATE_COPY_SOURCE;barrier.Transition.StateAfter=r->st;' in issue,'surface state restored after the copy'
    assert issue.index('ExecuteCommandLists')<issue.index('Signal(s_d3d.cmd_queue,s_cpu_surface_async_fence,++s_cpu_surface_async_fence_value)'),'fence signalled behind the copies'
    assert 's_cpu_surface_async_pending' not in issue and 'e->issued' not in issue,'copy bookkeeping stays in the tested refresh'
    pack=d3d[d3d.index('static void cpu_surface_async_pack'):d3d.index('static void cpu_surface_async_shutdown')]
    assert 'rsx_surface_readback_rows(e->back,' in pack and 'front' not in pack,'copies are packed off to the side, never into what readers see'
    assert 'GetCompletedValue(s_cpu_surface_async_fence)>=s_cpu_surface_async_fence_value' in d3d
    init=source[source.index('static void cpu_surface_init'):source.index('static void cpu_surface_retire')]
    assert init.index('cpu_surface_async_init();')<init.index('vm_read_sync_set_handler(s_cpu_surface_async_count?cpu_surface_request_async:cpu_surface_request,NULL);'),'handler chosen after the list is read'
    retire=source[source.index('static void cpu_surface_retire'):source.index('static void cpu_surface_shutdown')]
    assert retire.index('cpu_surface_async_retire(r);')<retire.index('cpu_surface_copy(r);')
    shutdown=source[source.index('static void cpu_surface_shutdown'):source.index('static void cpu_surface_capture')]
    assert shutdown.index('vm_read_sync_set_handler(NULL,NULL);')<shutdown.index('cpu_surface_async_shutdown();')<shutdown.index('cpu_surface_retire(')
    backend=(ROOT/'vendor/ps3recomp/libs/video/rsx_d3d12_backend.c').read_text(encoding='utf-8')
    body=backend[backend.index('static void render_frame(void)'):]
    body=body[:body.index('\nstatic ')]
    assert body.count('cpu_surface_async_refresh();')==1
    assert body.index('submit_recorded_commands();')<body.index('cpu_surface_batch_submitted();')<body.index('cpu_surface_async_refresh();')<body.index('cpu_surface_frame_complete();'),'refresh copies only submitted work'
    compiler=os.environ.get('MV_CLANG') or os.environ.get('CC') or ('clang-cl' if os.name=='nt' else 'cc')
    def build(name,core_text,glue_text,args=()):
        folder=out/name;folder.mkdir(exist_ok=True)
        c=folder/'check.c';c.write_text(fixture.replace('/* PRODUCTION_CORE */',core_text).replace('/* PRODUCTION_GLUE */',glue_text))
        exe=folder/('check.exe' if os.name=='nt' else 'check')
        if os.name=='nt':cmd=[compiler,'/nologo','/O2','/UNDEBUG','/I'+str(ROOT/'vendor/ps3recomp/libs/video'),str(c),'/Fo'+str(folder)+os.sep,'/Fe'+str(exe)]
        else:cmd=[compiler,'-std=c11','-O2','-UNDEBUG','-Wall','-Wextra','-Werror','-Wno-unused-function','-I'+str(ROOT/'vendor/ps3recomp/libs/video'),str(c),'-o',str(exe)]
        r=subprocess.run(cmd,capture_output=True,text=True);(folder/'build.log').write_text(r.stdout+r.stderr)
        assert r.returncode==0,r.stdout+r.stderr
        r=subprocess.run([str(exe),*args],capture_output=True,text=True);(folder/'run.log').write_text(r.stdout+r.stderr)
        return r
    r=build('actual',core,glue);print(r.stdout+r.stderr,end='')
    if r.returncode:return r.returncode
    for mode,message in [('bad-config','RSX_CPU_SURFACE_ASYNC'),('too-many','RSX_CPU_SURFACE_ASYNC'),('recording','async surface refresh before command submission')]:
        bad=subprocess.run([str(out/'actual'/('check.exe' if os.name=='nt' else 'check')),mode],capture_output=True,text=True)
        assert bad.returncode==2 and message in bad.stderr,(mode,bad.returncode,bad.stderr)
        print('PASS '+mode+' stops: '+bad.stderr.strip().splitlines()[-1])
    mutants=[('core','serves-straddling',' && (u64)address+bytes<=e->end)',')'),
             ('core','serves-unpublished','if(e->valid && (u64)address>=e->begin','if((u64)address>=e->begin'),
             ('core','wrong-shadow-offset','e->front+(size_t)((u64)address-e->begin)','e->front'),
             ('core','copies-without-reads','    return wanted?r:NULL;\n','    (void)wanted;return r;\n'),
             ('core','recopies-same-generation',' || r->gpu_generation==e->issued)return NULL;',')return NULL;'),
             ('core','keeps-old-surface','            if(e->slot!=i)cpu_surface_async_untrack(e);\n',''),
             ('glue','publishes-unfinished','        if(!cpu_surface_async_copy_done())return;','        (void)0;'),
             ('glue','retire-keeps-copies','        cpu_surface_async_copy_wait();\n        cpu_surface_async_untrack(e);\n','        cpu_surface_async_copy_wait();\n'),
             ('glue','retire-skips-wait','        cpu_surface_async_copy_wait();\n        cpu_surface_async_untrack(e);\n','        cpu_surface_async_untrack(e);\n')]
    for where,name,before,after in mutants:
        text=core if where=='core' else glue
        assert text.count(before)==1,(name,before)
        changed=text.replace(before,after)
        bad=build(name,changed if where=='core' else core,changed if where=='glue' else glue)
        assert bad.returncode!=0 and 'FAIL' in bad.stderr,(name,bad.returncode,bad.stdout,bad.stderr)
        print('PASS mutant '+name+' rejected: '+bad.stderr.strip().splitlines()[-1])
    print('PASS: async CPU surface reads and wiring ('+str(len(mutants))+' mutants rejected)');return 0
if __name__=='__main__':sys.exit(main())
