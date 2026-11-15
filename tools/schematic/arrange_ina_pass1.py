# Generates eda/INA.kicad_sch from the original backup: moves symbols, mirrors U1 and R3,
# replaces all wires/labels/junctions with the designed layout, adds the title block.
import re, uuid, sys
S='/private/tmp/claude-502/-Users-brycesalmiconsulting-dev-ina229/6e28b4ff-b4a4-478e-8882-db7dac4359b3/scratchpad/arrange2/'
SRC=S+'ina_00_orig.kicad_sch'
DST=sys.argv[1]
NS=uuid.UUID('6e28b4ff-b4a4-478e-8882-db7dac4359b3')
def U(key): return str(uuid.uuid5(NS,'INA:'+key))
UX,UY=133.35,129.54
def A(x,y): return (round(UX+x,2), round(UY+y,2))
def f(v): 
    s=('%.2f'%v).rstrip('0').rstrip('.'); return s if s!='-0' else '0'

lines=open(SRC).read().split('\n')
# split top-level blocks
blocks=[]; cur=None
for ln in lines:
    if cur is None:
        if ln.startswith('\t(') :
            if ln.count('(')==ln.count(')'): blocks.append(['single',[ln]])
            else: cur=[ln]
        else:
            blocks.append(['text',[ln]])
    else:
        cur.append(ln)
        if ln=='\t)':
            blocks.append(['multi',cur]); cur=None
assert cur is None

def kind(b):
    m=re.match(r'\t\((\S+)',b[0]); return m.group(1) if m else None

# symbol moves
P=-20.32; Pr=20.32
XV=P-5.08; XC=P-15.24; XG=P-27.94; XR=Pr+50.8
MOVES={
 ('INA229AIDGS',(127.0,82.55)):((UX,UY),False,'U1'),
 ('WSK2512',(177.8,120.65)):((UX,UY-50.8),True,'R3'),
 ('GCM188',(167.64,82.55)):((UX+XC,UY-22.86),False,'C1'),
 ('VCC',(167.64,74.93)):(None,False,'VCC_C1'),
 ('GND',(167.64,92.71)):((UX+XC,UY-17.78),False,'GND_C1'),
 ('GND',(152.4,95.25)):((UX+XG,UY+2.54),False,'GND_U1'),
 ('VCC',(157.48,64.77)):((UX+XV,UY-33.02),False,'VCC_VS'),
 ('ERJ-3EKF',(127.0,120.65)):((UX+XR,UY-7.62),False,'R1'),
 ('VCC',(127.0,110.49)):((UX+XR,UY-17.78),False,'VCC_R1'),
 ('ERJ-3EKF',(127.0,148.59)):((UX+Pr+25.4,UY-2.54),False,'R2'),
}
def atof_line(ln):
    m=re.match(r'\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)',ln)
    return m


ROT_OVERRIDE={'R2':'180','U1':'180'}
PROP_OVERRIDE={
 'U1':{'Reference':(0,-12.7,0,None),'Value':(0,-10.16,0,None)},
 'R3':{'Reference':(0,-59.69,0,None),'Value':(0,-57.15,0,None)},
 'C1':{'Reference':(XC-3.81,-24.13,90,'right'),'Value':(XC-3.81,-21.59,90,'right')},
 'R1':{'Reference':(XR+3.81,-8.89,90,'left'),'Value':(XR+3.81,-6.35,90,'left')},
 'R2':{'Reference':(Pr+25.4,-8.89,0,None),'Value':(Pr+25.4,-6.35,0,None)},
}
def set_prop(lines,pname,x,y,angle,justify):
    res=[]; i=0
    while i<len(lines):
        l=lines[i]
        if l.startswith('\t\t(property "%s"'%pname):
            blk=[l]; i+=1
            while lines[i]!='\t\t)':
                blk.append(lines[i]); i+=1
            blk.append(lines[i]); i+=1
            out2=[]; in_eff=False
            for bl in blk:
                if re.match(r'\t\t\t\(at ',bl):
                    out2.append('\t\t\t(at %s %s %s)'%(f(UX+x),f(UY+y),angle)); continue
                if re.match(r'\t\t\t\t\(justify ',bl): continue
                if bl=='\t\t\t(do_not_autoplace no)': bl='\t\t\t(do_not_autoplace yes)'
                if bl=='\t\t\t(effects': in_eff=True
                if bl=='\t\t\t)' and in_eff:
                    if justify: out2.append('\t\t\t\t(justify %s)'%justify)
                    in_eff=False
                out2.append(bl)
            res.extend(out2)
        else:
            res.append(l); i+=1
    return res
out=[]   # list of line-lists, in original order
moved=set()
for kindb,b in blocks:
    k=None
    if kindb=='multi' or kindb=='single':
        m=re.match(r'\t\((\S+)',b[0]); k=m.group(1) if m else None
    if k=='symbol':
        body=b
        lid=re.search(r'\(lib_id "([^"]+)"',' '.join(body)).group(1)
        atl=[l for l in body if re.match(r'\t\t\(at ',l)][0]
        m=atof_line(atl); old=(float(m.group(1)),float(m.group(2)))
        hit=[(v,k2) for k2,v in MOVES.items() if k2[0]==lid and abs(k2[1][0]-old[0])<1e-6 and abs(k2[1][1]-old[1])<1e-6]
        if not hit: out.append(body); continue
        (v,k2)=hit[0]; (new_at,mir,name)=v; moved.add(name)
        if new_at is None: continue
        rot=ROT_OVERRIDE.get(name, m.group(3) or '0')
        nx,ny=new_at; dx,dy=nx-old[0],ny-old[1]
        nb=[]
        for l in body:
            if l==atl:
                nb.append('\t\t(at %s %s %s)'%(f(nx),f(ny),rot))
                if mir: nb.append('\t\t(mirror y)')
                continue
            nb.append(l)
        res=[]; inprop=False
        for l in nb:
            if l.startswith('\t\t(property '): inprop=True
            elif l.startswith('\t\t(') : inprop=False
            mp=re.match(r'\t\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)$',l)
            if inprop and mp:
                l='\t\t\t(at %s %s %s)'%(f(float(mp.group(1))+dx),f(float(mp.group(2))+dy),mp.group(3) or '0')
            res.append(l)
        for pn,(px,py,pa,pj) in PROP_OVERRIDE.get(name,{}).items():
            res=set_prop(res,pn,px,py,pa,pj)
        out.append(res)
    elif k in ('wire','label','hierarchical_label','junction'):
        continue
    else:
        out.append(b)
assert moved==set(v[2] for v in MOVES.values()), moved
# ---- new wires / labels / junctions (relative to U1 origin)
new=[]
def wire(pts,key):
    for i in range(len(pts)-1):
        (x1,y1),(x2,y2)=A(*pts[i]),A(*pts[i+1])
        new.append(['wire',[
            '\t(wire',
            '\t\t(pts',
            '\t\t\t(xy %s %s) (xy %s %s)'%(f(x1),f(y1),f(x2),f(y2)),
            '\t\t)',
            '\t\t(stroke',
            '\t\t\t(width 0)',
            '\t\t\t(type default)',
            '\t\t)',
            '\t\t(uuid "%s")'%U('wire:'+key+str(i)),
            '\t)']])
def label(text,pt,angle,justify,key):
    x,y=A(*pt)
    new.append(['label',[
        '\t(label "%s"'%text,
        '\t\t(at %s %s %s)'%(f(x),f(y),angle),
        '\t\t(effects',
        '\t\t\t(font',
        '\t\t\t\t(size 1.27 1.27)',
        '\t\t\t)',
        '\t\t\t(justify %s bottom)'%justify,
        '\t\t)',
        '\t\t(uuid "%s")'%U('label:'+key),
        '\t)']])
def hlabel(text,pt,angle,justify,key):
    x,y=A(*pt)
    new.append(['hierarchical_label',[
        '\t(hierarchical_label "%s"'%text,
        '\t\t(shape bidirectional)',
        '\t\t(at %s %s %s)'%(f(x),f(y),angle),
        '\t\t(effects',
        '\t\t\t(font',
        '\t\t\t\t(size 1.27 1.27)',
        '\t\t\t)',
        '\t\t\t(justify %s)'%justify,
        '\t\t)',
        '\t\t(uuid "%s")'%U('hlabel:'+key),
        '\t)']])
def junction(pt,key):
    x,y=A(*pt)
    new.append(['junction',[
        '\t(junction',
        '\t\t(at %s %s)'%(f(x),f(y)),
        '\t\t(diameter 0)',
        '\t\t(color 0 0 0 0)',
        '\t\t(uuid "%s")'%U('junction:'+key),
        '\t)']])

sy=-50.8
wire([(-12.7,sy),(-50.8,sy)],'busin'); hlabel('BUS_IN',(-50.8,sy),180,'right','busin')
wire([(12.7,sy),(50.8,sy)],'busout_i2'); hlabel('BUS_OUT',(50.8,sy),0,'left','busout_i2')
ey=-25.4
wire([(-12.7,sy+2.54),(-12.7,ey)],'sp_e1'); label('INA.SENSE_P',(-12.7,ey),90,'left','sp_e1')
wire([(12.7,sy+2.54),(12.7,ey)],'sn_e2'); label('INA.SENSE_N',(12.7,ey),90,'left','sn_e2')
# IC left (rot 180): VS -5.08, GND -2.54, VBUS 0, IN- +2.54, IN+ +5.08
x1=P-5.08; x2=P-2.54; XE=P-20.32
wire([(P,2.54),(x1,2.54),(x1,7.62),(XE,7.62)],'inn'); label('INA.SENSE_N',(XE,7.62),0,'left','inn')
wire([(P,5.08),(x2,5.08),(x2,15.24),(XE,15.24)],'inp'); label('INA.SENSE_P',(XE,15.24),0,'left','inp')
wire([(P,0),(P-10.16,0)],'vbus'); hlabel('BUS_OUT',(P-10.16,0),180,'right','vbus')
wire([(P,-2.54),(XG,-2.54),(XG,2.54)],'gnd')
wire([(P,-5.08),(XV,-5.08),(XV,-33.02)],'vs')
wire([(XV,-25.4),(XC,-25.4)],'c1top'); junction((XV,-25.4),'c1')
# IC right (rot 180): SCLK -5.08, MISO -2.54, ALERT 0, MOSI +2.54, CS +5.08
wire([(Pr,-5.08),(Pr+2.54,-5.08),(Pr+2.54,-10.16),(Pr+7.62,-10.16)],'sclk'); hlabel('SPI.CLK',(Pr+7.62,-10.16),0,'left','sclk')
wire([(Pr,-2.54),(Pr+17.78,-2.54)],'miso_src'); label('INA.MISO_SRC',(Pr+2.54,-2.54),0,'left','miso_src')
wire([(Pr+30.48,-2.54),(Pr+33.02,-2.54)],'miso'); hlabel('SPI.MISO',(Pr+33.02,-2.54),0,'left','miso')
wire([(Pr,0),(XR,0)],'alert_a'); wire([(XR,0),(Pr+66.04,0)],'alert_b'); junction((XR,0),'alert')
hlabel('ALERT_N',(Pr+66.04,0),0,'left','alert')
wire([(XR,-12.7),(XR,-17.78)],'r1_top')
wire([(Pr,2.54),(Pr+5.08,2.54),(Pr+5.08,5.08),(Pr+10.16,5.08)],'mosi'); hlabel('SPI.MOSI',(Pr+10.16,5.08),0,'left','mosi')
wire([(Pr,5.08),(Pr+2.54,5.08),(Pr+2.54,10.16),(Pr+10.16,10.16)],'cs'); hlabel('SPI.CS',(Pr+10.16,10.16),0,'left','cs')

# assemble
final=[]
emitted_title=False
for b in out:
    final.extend(b)
    if b and b[0].startswith('\t(paper') and not emitted_title:
        pass
# title block: insert after the paper line
idx=[i for i,l in enumerate(final) if l.startswith('\t(paper')][0]
TB=['\t(title_block','\t\t(title "INA229HighSideMonitor")','\t\t(date "2026-11-15")','\t\t(rev "A")',
    '\t\t(company "Bryce Salmi Consulting")',
    '\t\t(comment 1 "github.com/kb1lqc/ina229 @ 6a0152a")',
    '\t\t(comment 2 "Reusable INA229 high-side monitor: shunt, decoupling, MISO term., ALERT_N pull-up")',
    '\t\t(comment 3 "Author: Bryce Salmi")','\t)']
final=final[:idx+1]+TB+final[idx+1:]
# new items go before the final closing paren of the file
close=max(i for i,l in enumerate(final) if l==')')
add=[]
for kk,ls in new: add.extend(ls)
final=final[:close]+add+final[close:]
open(DST,'w').write('\n'.join(final))
print('wrote',DST,len(final),'lines; new items',len(new))
