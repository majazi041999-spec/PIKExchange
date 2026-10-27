// Mechanical Telegram packaging of approved built-in ImageGen artwork.
// No artwork or text is changed here; only dimensions, encoding and previews.
const fs=require('node:fs/promises');
const path=require('node:path');
const sharp=require(process.env.SHARP_MODULE_PATH||'sharp');
const root=path.resolve('assets/stickers/redesign');
(async()=>{
  const catalog=JSON.parse(await fs.readFile(path.join(root,'catalog.json'),'utf8'));
  await fs.mkdir(path.join(root,'stickers'),{recursive:true});
  const tiles=[];
  for(const item of catalog.stickers){
    const original=path.join(root,'source',item.key+'.png');
    try{await fs.access(original)}catch{continue}
    const buffer=await sharp(original).resize(512,512,{fit:'contain',background:'#00000000'}).png().toBuffer();
    await fs.writeFile(path.join(root,'stickers',item.key+'.png'),buffer);
    const target=path.join(root,'stickers',item.key+'.webp');
    await sharp(buffer).webp({quality:96,alphaQuality:100,effort:6}).toFile(target);
    const info=await fs.stat(target);
    if(info.size>512*1024)throw Error('Oversize sticker: '+item.key);
    const raw=await sharp(buffer).ensureAlpha().raw().toBuffer();
    if(raw[3]!==0)throw Error('Outer corner is not transparent: '+item.key);
    tiles.push({input:await sharp(buffer).resize(244,244).toBuffer(),left:(tiles.length%4)*264+10,top:Math.floor(tiles.length/4)*264+10});
    console.log(item.key,info.size);
  }
  if(tiles.length){
    await sharp({create:{width:1056,height:Math.ceil(tiles.length/4)*264,channels:4,background:'#dfe5e8'}}).composite(tiles).png().toFile(path.join(root,'preview.png'));
  }
  if(tiles.length===catalog.stickers.length){
    await sharp(path.join(root,'stickers/brand.png')).resize(100,100).png().toFile(path.join(root,'thumbnail.png'));
  }
})();
