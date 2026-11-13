"""Make local wheel artwork from the user's own game. No textures are shipped with Longshot."""
import argparse
from pathlib import Path
import struct,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

def prepare_image(image,name):
    from PIL import Image
    image=image.convert("RGBA")
    if name.startswith("SelectItem"):
        # R8 borders/glows encode opacity in red; convert their mask, not an opaque black rectangle.
        alpha=image.getchannel("A") if name=="SelectItemBase_00" else image.getchannel("R")
        image=Image.new("RGBA",image.size,(255,255,255,255) if name=="SelectItemBase_00" else (205,180,128,255))
        image.putalpha(alpha)
        image.thumbnail((256,256),Image.Resampling.LANCZOS)
    else:
        image.thumbnail((192,192),Image.Resampling.LANCZOS)
    return image

def extract(romfs,out):
    from PIL import Image
    import prompt_assets as assets
    from shader_census import dictionaries,unpack
    import zstandard
    romfs=Path(romfs);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    dictionaries_=dictionaries(str(romfs),zstandard)
    def write(raw,name,t):
        image=prepare_image(assets.decode(raw,t),name)
        (out/(name+".rgba")).write_bytes(struct.pack("<II",*image.size)+image.tobytes())
    count=0
    for path in sorted((romfs/"UI/Tex/Icon").glob("*.bntx.zs")):
        raw=unpack(path.read_bytes(),dictionaries_,zstandard)
        for name,t in assets.bntx_textures(raw).items():
            if t[1]>>8==0x2d:
                write(raw,path.name[:-8],t);count+=1;break
    path=romfs/assets.TEXTURES
    if path.exists():
        raw=unpack(path.read_bytes(),dictionaries_,zstandard)
        for name,t in assets.bntx_textures(raw).items():
            if name in ("SelectItemBase_00","SelectItemBase_01","SelectItemBaseGlow_00"):
                write(raw,name,t);count+=1
    print("PC wheel: %d local icons"%count)
    return count

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--romfs",required=True);parser.add_argument("--out",required=True)
    args=parser.parse_args();extract(args.romfs,args.out)
if __name__=="__main__":main()
