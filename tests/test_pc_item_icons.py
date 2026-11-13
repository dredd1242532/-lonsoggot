"""The game's monochrome menu borders must become transparent masks, not black squares."""
from pathlib import Path
import sys
RUN_ALL_MODULES=["PIL"]
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"nx2"))
from PIL import Image
from pc_item_icons import prepare_image
for name in ("SelectItemBase_01","SelectItemBaseGlow_00"):
    image=Image.new("RGBA",(2,1));image.putdata([(0,0,0,255),(255,255,255,255)])
    result=prepare_image(image,name);pixels=[result.getpixel((x,0)) for x in range(2)]
    assert pixels[0][3]==0 and pixels[1][3]==255,(name,pixels)
base=Image.new("RGBA",(2,1));base.putdata([(12,34,56,0),(12,34,56,255)])
assert list(prepare_image(base,"SelectItemBase_00").getchannel("A").tobytes())==[0,255]
item=Image.new("RGBA",(1,1),(12,34,56,78));assert prepare_image(item,"Item_Test").getpixel((0,0))==(12,34,56,78)
assert max(prepare_image(Image.new("RGBA",(1024,512)),"Item_Test").size)<=192
print("PC wheel artwork: transparent mask endpoints, original alpha and item colors retained")
