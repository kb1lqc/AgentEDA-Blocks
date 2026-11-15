import re,uuid,sys
src,dst=sys.argv[1:3]
NS=uuid.UUID('6e28b4ff-b4a4-478e-8882-db7dac4359b3')
U=lambda k:str(uuid.uuid5(NS,'INA4:'+k))
t=open(src).read()
# split top-level blocks
lines=t.split('\n'); blocks=[];cur=None
for ln in lines:
    if cur is None:
        if ln.startswith('\t('):
            if ln.count('(')==ln.count(')'): blocks.append(ln)
            else: cur=[ln]
        else: blocks.append(ln)
    else:
        cur.append(ln)
        if ln=='\t)': blocks.append('\n'.join(cur)); cur=None
out=[]
def shift(b,dx,dy):
    def r(m):
        x=round(float(m.group(1))+dx,2);y=round(float(m.group(2))+dy,2)
        return '(at %g %g%s)'%(x,y,m.group(3) or '')
    return re.sub(r'\(at ([-\d.]+) ([-\d.]+)((?: [-\d.]+)?)\)',r,b)
def setprop(b,name,x,y,a):
    return re.sub(r'(\(property "%s"[^\n]*\n\t\t\t)\(at [-\d.]+ [-\d.]+ [-\d.]+\)'%name,lambda m:m.group(1)+'(at %g %g %g)'%(x,y,a),b,count=1)
def wire(a,b,k): return '\t(wire\n\t\t(pts\n\t\t\t(xy %g %g) (xy %g %g)\n\t\t)\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "%s")\n\t)'%(a[0],a[1],b[0],b[1],U(k))
def hl(name,x,y,k): return '\t(hierarchical_label "%s"\n\t\t(shape bidirectional)\n\t\t(at %g %g 0)\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify left)\n\t\t)\n\t\t(uuid "%s")\n\t)'%(name,x,y,U(k))
XF=193.04; XR1=162.56
newwires=[
 ((153.67,124.46),(156.21,124.46)),((156.21,124.46),(156.21,104.14)),((156.21,104.14),(XF,104.14)),
 ((153.67,127),(158.75,127)),((158.75,127),(158.75,109.22)),((158.75,109.22),(175.26,109.22)),((187.96,109.22),(XF,109.22)),
 ((153.67,129.54),(XF,129.54)),
 ((153.67,132.08),(XF,132.08)),((153.67,134.62),(XF,134.62)),
]
for b in blocks:
    m=re.match(r'\t\((\S+)',b); k=m.group(1) if m else None
    if k=='wire':
        p=[float(v) for xy in re.findall(r'xy ([-\d.]+) ([-\d.]+)',b) for v in xy]
        if all(p[i]>=153.6 for i in (0,2)) and all(p[i]>=105 for i in (1,3)): continue
    if k=='junction' and '(at 204.47 129.54)' in b: continue
    if k=='label' and 'INA.MISO_SRC' in b:
        out.append(b.replace('(at 156.21 127 0)','(at 160.02 109.22 0)')); continue
    if k=='hierarchical_label':
        nm=re.match(r'\t\(hierarchical_label "([^"]*)"',b).group(1); at=re.search(r'\(at ([-\d.]+) ([-\d.]+)',b)
        pos={'SPI.CLK':104.14,'SPI.MISO':109.22,'ALERT_N':129.54,'SPI.MOSI':132.08,'SPI.CS':134.62}
        if nm in pos and float(at.group(1))>150:
            out.append(re.sub(r'\(at [-\d.]+ [-\d.]+ 0\)','(at %g %g 0)'%(XF,pos[nm]),b,count=1)); continue
    if k=='symbol':
        if '(lib_id "ERJ-3EKF")' in b and '(at 204.47 121.92 270)' in b:
            b=shift(b,XR1-204.47,0)
            b=setprop(b,'Reference',165.1,120.65,90); b=setprop(b,'Value',165.1,123.19,90)
        elif '(lib_id "VCC")' in b and '(at 204.47 111.76 0)' in b:
            b=shift(b,XR1-204.47,116.84-111.76)
        elif '(lib_id "ERJ-3EKF")' in b and '(at 179.07 127 180)' in b:
            b=shift(b,3.81,109.22-127)
            b=setprop(b,'Reference',181.61,113.03,0); b=setprop(b,'Value',181.61,115.57,0)
    out.append(b)
# insert new wires + junction before the first label block
extra=[wire(a,b,'w%d'%i) for i,(a,b) in enumerate(newwires)]
extra.append(wire((175.26,109.22),(175.26,109.22),'x')) if False else None
extra=[e for e in extra if e]
# R2 pin wires: left pin 175.26, right pin 190.5 handled via wire spans above
extra.append('\t(junction\n\t\t(at %g 129.54)\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n\t\t(uuid "%s")\n\t)'%(XR1,U('j1')))
extra.append(wire((XR1,129.54),(XR1,129.54),'dummy')) if False else None
extra=[e for e in extra if e]
idx=next(i for i,b in enumerate(out) if b.startswith('\t(label '))
out[idx:idx]=extra
open(dst,'w').write('\n'.join(out))
