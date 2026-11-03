"""Bounded authored GPU regression and exact old-descriptor negative control."""
import subprocess,sys
exe=sys.argv[1]
for args,code,passed,failed in [(sys.argv[2:]+mode+old,code,passed,failed)
 for mode in [[],['--blend-policy']]
 for old,code,passed,failed in [([],0,7,0),(['--unpatched'],1,2,5)]]:
 r=subprocess.run([exe,*args],capture_output=True,text=True,timeout=30)
 print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr)
 if r.returncode!=code or r.stdout.count(' PASS')!=passed or r.stdout.count(' FAIL')!=failed:
  raise SystemExit('MRT pixel or negative-control mismatch')
