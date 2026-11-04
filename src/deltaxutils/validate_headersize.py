import struct, glob, os, sys, importlib.util

sys.path.insert(0, r'D:\粉刷匠大作战\2608新的逆向计划！\Gamma引擎相关')
import gamma_formats as gf

KEY=bytes([0xE7,0x9B,0x9B,0x9C,0x4E,0xD6,0x44,0x50,0xB3,0x53,0x8D,0xF1,0x38,0xCC,0xCE,0xA9])
MAGICS={b'tex\0',b'ams\0',b'ans\0',b'anf\0',b'mtr\0',b'gfx\0',b'gui\0',b'eft\0',b'map\0'}

def entries(p):
    b=open(p,'rb').read(); magic,ver,c,o=struct.unpack_from('<4sIII',b,0)
    out=[]
    for i in range(c):
        h,hs,off,sz,z=struct.unpack_from('<IIIII',b,o+i*20)
        d=b[off:off+sz]
        if ver: d=bytes(x^KEY[(off+j)&15] for j,x in enumerate(d))
        out.append((h,hs,off,sz,d))
    return ver,out

tot=0; ok=0; bad=[]; raw=0; unknown=0
for p in sorted(glob.glob(r'D:\game\guyu2\data\*.pkg')):
    ver,es=entries(p)
    for i,(h,hs,off,sz,d) in enumerate(es):
        tot+=1
        if d[:4] in MAGICS:
            try:
                head,end=gf.CommonFileHead.parse(d,0)
                exp=end+head.extra_size
                if exp==hs: ok+=1
                else: bad.append((os.path.basename(p),i,hex(h),hs,exp,d[:4]))
            except Exception as e:
                bad.append((os.path.basename(p),i,hex(h),hs,'ERR',str(e)))
        else:
            if hs==0: raw+=1
            else: unknown+=1
print('total',tot,'ok',ok,'bad',len(bad),'raw(headerSize=0)',raw,'non4cc with hs>0',unknown)
for x in bad[:20]: print(x)
